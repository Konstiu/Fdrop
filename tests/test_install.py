"""install.sh and uninstall.sh against a temporary HOME and mocked system commands.

PATH holds only the mocks and a short allowlist of real tools, so a command
the scripts were not expected to run is not found rather than run for real.
sudo, dnf, rpm, rpmbuild, systemctl and omdrop are mocks that log each call;
git is the real one behind a logging wrapper, fetching from a local repository.
"""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_TOOLS = """bash sh env cat sed grep awk gawk tr head tail sort uniq wc printf mktemp
    mkdir rmdir rm mv cp ln readlink dirname basename ls find date tar gzip python3
    touch chmod true false""".split()
PKG = "/usr/share/omdrop"

MOCKS = {
    "sudo": 'log sudo "$@"; exec "$@"',
    "dnf": r'''log dnf "$@"
[[ -n ${MOCK_DNF_FAIL:-} ]] && exit 1
verb=$1; shift
for a; do
  [[ $a == -* ]] && continue
  name=${a##*/}; name=${name%%-[0-9]*}
  if [[ $verb == install ]]; then echo "$name" >> "$MOCK_STATE/installed"
  else grep -vx "$name" "$MOCK_STATE/installed" > "$MOCK_STATE/i.tmp"; mv "$MOCK_STATE/i.tmp" "$MOCK_STATE/installed"
  fi
done''',
    "rpm": r'''log rpm "$@"
name=${@: -1}
if grep -qx "$name" "$MOCK_STATE/installed" 2>/dev/null; then
  [[ $* == *--quiet* ]] || echo "$name-1.0-1.fc44.noarch"; exit 0
fi
[[ $* == *--quiet* ]] || echo "package $name is not installed"; exit 1''',
    "rpmbuild": r'''log rpmbuild "$@"
[[ -n ${MOCK_RPMBUILD_FAIL:-} ]] && { echo "error: Bad exit status from %check" >&2; exit 1; }
top='' snap=''
while (( $# )); do
  case $1 in
    --define) case $2 in _topdir\ *) top=${2#_topdir };; omdrop_snap\ *) snap=.${2#omdrop_snap };; esac; shift 2 ;;
    *) spec=$1; shift ;;
  esac
done
name=$(basename "$spec" .spec)
mkdir -p "$top/RPMS/noarch"
touch "$top/RPMS/noarch/$name-1.0-1$snap.fc44.noarch.rpm"''',
    "systemctl": r'''log systemctl "$@"
case $2 in
  is-active) [[ -e $MOCK_STATE/active ]] && { echo active; exit 0; }; echo inactive; exit 3 ;;
  is-enabled) echo disabled; exit 1 ;;
  stop) rm -f "$MOCK_STATE/active" ;;
esac''',
    "omdrop": r'''log omdrop "$@"
[[ $1 == off ]] && rm -f "$MOCK_STATE/active"; exit 0''',
    "firewall-cmd": 'log firewall-cmd "$@"',
    "update-desktop-database": 'log update-desktop-database "$@"',
    "update-mime-database": 'log update-mime-database "$@"',
    "desktop-file-validate": 'log desktop-file-validate "$@"',
    "appstreamcli": 'log appstreamcli "$@"',
}

UNIT_OURS = f"""[Service]
WorkingDirectory={PKG}
ExecStart=/usr/bin/python3 {PKG}/bin/airdrop-serve.py --iface awdl0 --port 8771
"""
UNIT_CHECKOUT = UNIT_OURS.replace(PKG, "/home/someone/omdrop-plugin")
LINK_APP_OURS = f"""[Desktop Entry]
Type=Application
Exec="{PKG}/bin/omdrop" open-link %f
"""
LINK_MIME = """<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
  <mime-type type="application/x-webloc">
    <comment>Website location</comment>
    <glob pattern="*.webloc"/>
  </mime-type>
</mime-info>
"""
MIMEAPPS = """[Default Applications]
image/png=org.gnome.Loupe.desktop
application/x-mswinurl=netmojo.omdrop-links.desktop
application/x-webloc=netmojo.omdrop-links.desktop

[Added Associations]
application/x-webloc=netmojo.omdrop-links.desktop
"""


def git(*args, cwd=None):
    env = {"PATH": os.environ["PATH"], "HOME": cwd or "/nonexistent", "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True,
                          capture_output=True, text=True).stdout.strip()


def snapshot(*roots):
    """Every path under the roots: kind, mode, mtime, link target or content hash."""
    seen = {}
    for root in roots:
        for d, dirs, files in os.walk(root):
            for name in dirs + files:
                p = os.path.join(d, name)
                st = os.lstat(p)
                if os.path.islink(p):
                    what = "link:" + os.readlink(p)
                elif os.path.isdir(p):
                    what = "dir"
                else:
                    with open(p, "rb") as f:
                        what = hashlib.sha256(f.read()).hexdigest()
                seen[p] = (what, st.st_mode, st.st_mtime_ns)
        seen[root] = ("root", os.lstat(root).st_mode, os.lstat(root).st_mtime_ns)
    return seen


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fdrop-test-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = self.mk("home")
        self.state = self.mk("state")
        self.tmpdir = self.mk("tmp")
        self.log = os.path.join(self.tmp, "calls.log")
        open(self.log, "w").close()
        self.installed("omdrop-owl")

        self.sysbin = self.mk("sysbin")
        for tool in REAL_TOOLS:
            path = shutil.which(tool)
            if path:
                os.symlink(path, os.path.join(self.sysbin, tool))
        self.mockbin = self.mk("mockbin")
        for name, body in MOCKS.items():
            self.mock(name, body)
        real_git = shutil.which("git")
        self.mock("git", f'log git "$@"; exec {real_git} "$@"')

        self.os_release = os.path.join(self.tmp, "os-release")
        self.write(self.os_release, 'NAME="Fedora Linux"\nID=fedora\nVERSION_ID=44\n')

        # Upstream omdrop-plugin, as a local repository to fetch from.
        self.upstream = self.mk("upstream")
        git("init", "-q", cwd=self.upstream)
        self.write(os.path.join(self.upstream, "manifest.json"), json.dumps({"version": "0.16.1"}))
        git("add", ".", cwd=self.upstream)
        git("commit", "-qm", "upstream", cwd=self.upstream)
        self.sha = git("rev-parse", "HEAD", cwd=self.upstream)

        # This repository, with the pin pointing at that commit.
        self.repo = os.path.join(self.tmp, "fdrop")
        shutil.copytree(ROOT, self.repo, symlinks=True,
                        ignore=shutil.ignore_patterns(".git", "dist", "__pycache__"))
        self.pin(self.sha)

    def mk(self, *parts):
        p = os.path.join(self.tmp, *parts)
        os.makedirs(p, exist_ok=True)
        return p

    def write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def read(self, path):
        with open(path) as f:
            return f.read()

    def mock(self, name, body):
        path = os.path.join(self.mockbin, name)
        self.write(path, '#!/bin/bash\nlog() { local IFS=" "; echo "$*" >> "$MOCK_LOG"; }\n' + body + "\n")
        os.chmod(path, 0o755)

    def unmock(self, name):
        os.remove(os.path.join(self.mockbin, name))

    def installed(self, *pkgs):
        self.write(os.path.join(self.state, "installed"), "".join(p + "\n" for p in pkgs))

    def installed_now(self):
        return self.read(os.path.join(self.state, "installed")).split()

    def pin(self, sha):
        self.write(os.path.join(self.repo, "packaging", "omdrop-revision"), f"# pinned\n{sha}\n")

    def h(self, *parts):
        return os.path.join(self.home, *parts)

    def run_script(self, script, *args, answers="", **env):
        e = {"HOME": self.home, "PATH": f"{self.mockbin}:{self.sysbin}", "TMPDIR": self.tmpdir,
             "LANG": "C", "MOCK_LOG": self.log, "MOCK_STATE": self.state,
             "FDROP_OS_RELEASE": self.os_release, "OMDROP_REPO": self.upstream,
             "OMDROP_SKIP_CHECK": "1", "GIT_CONFIG_NOSYSTEM": "1"}
        e.update(env)
        return subprocess.run(["/bin/bash", os.path.join(self.repo, script), *args],
                              input=answers, env=e, capture_output=True, text=True, timeout=120)

    def calls(self):
        return self.read(self.log).splitlines()

    def called(self, prefix):
        return [c for c in self.calls() if c.startswith(prefix)]

    def assertOk(self, r):
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def assertFailed(self, r, *phrases):
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        for phrase in phrases:
            self.assertIn(phrase, r.stderr)

    def assertNothingSystemWide(self):
        """No privileged, building, fetching or service-changing command ran."""
        for c in self.calls():
            self.assertFalse(c.startswith(("sudo", "dnf", "rpmbuild", "git", "omdrop",
                                           "firewall-cmd", "update-")), c)
            if c.startswith("systemctl"):
                self.assertIn(c.split()[2], ("is-active", "is-enabled"), c)

    def populate_home(self):
        """Everything an installed and used omdrop leaves in a home directory."""
        self.write(self.h(".config/systemd/user/airdrop-receiver.service"), UNIT_OURS)
        self.write(self.h(".config/systemd/user/airdrop-receiver.service.d/10-download-dir.conf"),
                   "# Written by omdrop. Declares exactly the paths the receiver may write.\n[Service]\n")
        self.write(self.h(".config/systemd/user/unrelated.service"), "[Service]\nExecStart=/bin/true\n")
        self.write(self.h(".local/share/applications/netmojo.omdrop-links.desktop"), LINK_APP_OURS)
        self.write(self.h(".local/share/applications/other.desktop"), "[Desktop Entry]\n")
        self.write(self.h(".local/share/mime/packages/netmojo.omdrop-webloc.xml"), LINK_MIME)
        self.write(self.h(".config/mimeapps.list"), MIMEAPPS)
        os.makedirs(self.h(".claude/skills"))
        os.symlink(f"{PKG}/skills/omdrop", self.h(".claude/skills/omdrop"))
        self.write(self.h(".config/airdrop/config.toml"), 'name = "laptop"\n')
        self.write(self.h(".config/omdrop/settings"), "sound=off\n")
        self.write(self.h(".config/omdrop/known-senders"), "abc\n")
        for name in ("certificate.pem", "key.pem", "validation_record.cms"):
            self.write(self.h(".omdrop/keys", name), "secret " + name)
        self.write(self.h("Downloads/photo.jpg"), "jpeg")
        self.write(self.h(".opendrop/keys/key.pem"), "old")


class ArgumentTests(Base):
    def test_help(self):
        for script in ("install.sh", "uninstall.sh"):
            for flag in ("--help", "-h"):
                r = self.run_script(script, flag)
                self.assertOk(r)
                self.assertIn("Usage:", r.stdout)
                self.assertIn("--dry-run", r.stdout)
        self.assertIn("--all", self.run_script("uninstall.sh", "--help").stdout)
        self.assertEqual(self.calls(), [])

    def test_unknown_options_are_refused_before_anything_runs(self):
        for script, args in (("install.sh", ["--all"]), ("install.sh", ["--dryrun"]),
                             ("install.sh", ["extra"]), ("uninstall.sh", ["--force"]),
                             ("uninstall.sh", ["--all", "now"])):
            r = self.run_script(script, *args)
            self.assertEqual(r.returncode, 2, (script, args))
            self.assertIn("Unknown option", r.stderr)
            self.assertIn("Usage:", r.stderr)
        self.assertEqual(self.calls(), [])

    def test_options_combine_in_any_order(self):
        self.assertOk(self.run_script("uninstall.sh", "--dry-run", "--all"))
        self.assertOk(self.run_script("uninstall.sh", "--all", "--dry-run"))
        self.assertNothingSystemWide()


class InstallDryRunTests(Base):
    def test_dry_run_changes_nothing_and_says_what_it_would_do(self):
        self.populate_home()
        os.makedirs(self.h(".local/bin"))
        os.symlink("/home/someone/omdrop-plugin/bin/omdrop", self.h(".local/bin/omdrop"))
        before = snapshot(self.home, self.repo, self.tmpdir, self.upstream, self.state)

        r = self.run_script("install.sh", "--dry-run")

        self.assertOk(r)
        self.assertEqual(snapshot(self.home, self.repo, self.tmpdir, self.upstream, self.state), before)
        self.assertNothingSystemWide()
        out = r.stdout
        self.assertIn(f"fetch -q --depth 1 --no-tags {self.upstream} {self.sha}", out)
        self.assertIn("tools/build-rpm.sh", out)
        self.assertIn("would run: sudo dnf install", out)
        self.assertIn("known only after the build", out)
        self.assertIn("would ask: Move them aside", out)
        self.assertIn("would run if you answer yes: mv -T --", out)
        self.assertIn("~/.local/bin/omdrop -> /home/someone/omdrop-plugin/bin/omdrop", out)
        self.assertIn("Nothing was changed", out)

    def test_dry_run_still_reports_a_missing_backend(self):
        self.installed()
        r = self.run_script("install.sh", "--dry-run")
        self.assertFailed(r, "omdrop-owl is not installed")
        self.assertNothingSystemWide()


class InstallPreflightTests(Base):
    def assertStoppedEarly(self, r, *phrases):
        self.assertFailed(r, *phrases)
        self.assertNothingSystemWide()
        self.assertEqual(os.listdir(self.tmpdir), [])

    def test_not_fedora(self):
        self.write(self.os_release, "ID=debian\n")
        self.assertStoppedEarly(self.run_script("install.sh"), "for Fedora", "ID=debian")

    def test_missing_tools_are_named_with_their_packages(self):
        self.unmock("desktop-file-validate")
        self.unmock("appstreamcli")
        r = self.run_script("install.sh")
        self.assertStoppedEarly(r, "sudo dnf install appstream desktop-file-utils")

    def test_missing_rpmbuild(self):
        self.unmock("rpmbuild")
        self.assertStoppedEarly(self.run_script("install.sh"), "rpm-build")

    def test_omdrop_owl_must_already_be_installed(self):
        self.installed()
        self.assertStoppedEarly(self.run_script("install.sh"), "omdrop-owl is not installed",
                                "t4t5/omdrop-owl")

    def test_pin_must_be_a_full_sha(self):
        for bad in (self.sha[:7], "main", "", self.sha + "\n" + self.sha):
            self.pin(bad)
            self.assertStoppedEarly(self.run_script("install.sh"), "40-character")


class InstallTests(Base):
    def test_builds_the_pinned_commit_and_installs_exactly_that(self):
        self.populate_home()
        personal = {p: v for p, v in snapshot(self.home).items()}

        r = self.run_script("install.sh")

        self.assertOk(r)
        snap = "git" + self.sha[:7]
        rpmbuild = self.called("rpmbuild")
        self.assertEqual(len(rpmbuild), 2)
        self.assertIn(f"omdrop_snap {snap}", rpmbuild[0])
        [install] = self.called("sudo dnf install")
        files = install.split()[3:]
        self.assertEqual([os.path.basename(f) for f in files],
                         [f"omdrop-1.0-1.{snap}.fc44.noarch.rpm", "fdrop-1.0-1.fc44.noarch.rpm"])
        self.assertTrue(all(f.startswith(self.tmpdir) for f in files))
        self.assertIn("omdrop-owl", self.installed_now())
        self.assertEqual(snapshot(self.home), personal)
        self.assertEqual(os.listdir(self.tmpdir), [], "the work directory is removed")
        self.assertFalse(os.path.exists(os.path.join(self.repo, "dist")))

    def test_old_links_step_is_hidden_when_there_are_none(self):
        os.makedirs(self.h(".local/bin"))
        os.symlink("/usr/bin/omdrop", self.h(".local/bin/omdrop"))
        for args in ([], ["--dry-run"]):
            r = self.run_script("install.sh", *args)
            self.assertOk(r)
            self.assertNotIn("Old links", r.stdout)

    def test_a_failed_build_installs_nothing(self):
        r = self.run_script("install.sh", MOCK_RPMBUILD_FAIL="1")
        self.assertFailed(r, "Building the RPMs failed", "Nothing was installed")
        self.assertEqual(self.called("sudo"), [])
        self.assertEqual(self.called("dnf"), [])
        self.assertEqual(os.listdir(self.tmpdir), [])

    def test_a_commit_that_cannot_be_fetched_stops_before_building(self):
        self.pin("0123456789abcdef0123456789abcdef01234567")
        r = self.run_script("install.sh")
        self.assertFailed(r, "Could not fetch omdrop commit", "Nothing was installed")
        self.assertEqual(self.called("rpmbuild"), [])
        self.assertEqual(self.called("sudo"), [])

    def test_dnf_failing_is_reported_and_home_is_left_alone(self):
        os.makedirs(self.h(".local/bin"))
        os.symlink("/home/someone/omdrop-plugin/bin/omdrop", self.h(".local/bin/omdrop"))
        r = self.run_script("install.sh", answers="y\n", MOCK_DNF_FAIL="1")
        self.assertFailed(r, "dnf did not install")
        self.assertTrue(os.path.islink(self.h(".local/bin/omdrop")))


class LegacyLinkTests(Base):
    def setUp(self):
        super().setUp()
        self.bindir = self.h(".local/bin")
        os.makedirs(self.bindir)
        os.symlink("/home/someone/omdrop-plugin/bin/omdrop", os.path.join(self.bindir, "omdrop"))
        os.symlink("/home/someone/fdrop/fdrop", os.path.join(self.bindir, "fdrop"))
        os.symlink("/usr/bin/omdrop", os.path.join(self.bindir, "omdrop-gnome"))  # harmless
        os.symlink("/somewhere/else", os.path.join(self.bindir, "unrelated"))

    def test_moving_them_aside_can_be_undone(self):
        r = self.run_script("install.sh", answers="y\n")
        self.assertOk(r)
        self.assertEqual(sorted(os.listdir(self.bindir)), ["omdrop-gnome", "unrelated"])
        base = self.h(".local/share/fdrop/replaced-links")
        [stamp] = os.listdir(base)
        backup = os.path.join(base, stamp)
        self.assertEqual(os.readlink(os.path.join(backup, "omdrop")),
                         "/home/someone/omdrop-plugin/bin/omdrop")
        restore = self.read(os.path.join(backup, "RESTORE"))
        self.assertIn("To undo", r.stdout)
        # The recorded commands put the links back exactly.
        for line in restore.splitlines()[1:]:
            subprocess.run(line, shell=True, check=True, executable="/bin/bash")
        self.assertEqual(os.readlink(os.path.join(self.bindir, "omdrop")),
                         "/home/someone/omdrop-plugin/bin/omdrop")
        self.assertEqual(os.readlink(os.path.join(self.bindir, "fdrop")), "/home/someone/fdrop/fdrop")

    def test_declining_or_no_answer_keeps_them(self):
        for answers in ("n\n", ""):
            r = self.run_script("install.sh", answers=answers)
            self.assertOk(r)
            self.assertIn("Left them in place", r.stdout)
            self.assertTrue(os.path.islink(os.path.join(self.bindir, "omdrop")))
            self.assertFalse(os.path.exists(self.h(".local/share/fdrop")))

    def test_a_file_is_never_moved(self):
        os.remove(os.path.join(self.bindir, "omdrop"))
        self.write(os.path.join(self.bindir, "omdrop"), "#!/bin/sh\n")
        r = self.run_script("install.sh", answers="y\n")
        self.assertOk(r)
        self.assertIn("Left ~/.local/bin/omdrop alone", r.stdout)
        self.assertTrue(os.path.isfile(os.path.join(self.bindir, "omdrop")))
        self.assertNotIn("~/.local/bin/omdrop ->", r.stdout)
        self.assertIn("~/.local/bin/fdrop ->", r.stdout)


class UninstallTests(Base):
    def setUp(self):
        super().setUp()
        self.installed("omdrop-owl", "omdrop", "fdrop")
        self.populate_home()

    def assertKeptPersonal(self):
        for p in (".config/airdrop/config.toml", ".config/omdrop/settings", ".omdrop/keys/key.pem",
                  ".omdrop/keys/certificate.pem", "Downloads/photo.jpg", ".opendrop/keys/key.pem",
                  ".config/systemd/user/unrelated.service", ".local/share/applications/other.desktop"):
            self.assertTrue(os.path.exists(self.h(p)), p)

    def test_default_removes_packages_and_what_points_into_them(self):
        r = self.run_script("uninstall.sh")
        self.assertOk(r)
        self.assertEqual(self.called("sudo"), ["sudo dnf remove --no-autoremove fdrop omdrop"])
        self.assertEqual(self.installed_now(), ["omdrop-owl"])
        for p in (".config/systemd/user/airdrop-receiver.service",
                  ".local/share/applications/netmojo.omdrop-links.desktop",
                  ".local/share/mime/packages/netmojo.omdrop-webloc.xml", ".claude/skills/omdrop"):
            self.assertFalse(os.path.lexists(self.h(p)), p)
        self.assertIn("systemctl --user daemon-reload", self.calls())
        mimeapps = self.read(self.h(".config/mimeapps.list"))
        self.assertNotIn("application/x-mswinurl=netmojo", mimeapps)
        self.assertIn("image/png=org.gnome.Loupe.desktop", mimeapps)
        # Only [Default Applications] is edited.
        self.assertIn("[Added Associations]\napplication/x-webloc=netmojo.omdrop-links.desktop", mimeapps)
        self.assertKeptPersonal()
        self.assertTrue(os.path.exists(self.h(".config/systemd/user/airdrop-receiver.service.d/10-download-dir.conf")))
        self.assertTrue(os.path.isdir(self.h(".claude/skills")))

    def test_files_from_another_omdrop_are_kept(self):
        self.write(self.h(".config/systemd/user/airdrop-receiver.service"), UNIT_CHECKOUT)
        self.write(self.h(".local/share/applications/netmojo.omdrop-links.desktop"),
                   LINK_APP_OURS.replace(PKG, "/home/someone/omdrop-plugin"))
        os.remove(self.h(".claude/skills/omdrop"))
        os.symlink("/home/someone/omdrop-plugin/skills/omdrop", self.h(".claude/skills/omdrop"))
        r = self.run_script("uninstall.sh")
        self.assertOk(r)
        self.assertEqual(self.read(self.h(".config/systemd/user/airdrop-receiver.service")), UNIT_CHECKOUT)
        self.assertTrue(os.path.exists(self.h(".local/share/applications/netmojo.omdrop-links.desktop")))
        # The handler still in use keeps its MIME type and defaults.
        self.assertTrue(os.path.exists(self.h(".local/share/mime/packages/netmojo.omdrop-webloc.xml")))
        self.assertEqual(self.read(self.h(".config/mimeapps.list")), MIMEAPPS)
        self.assertTrue(os.path.islink(self.h(".claude/skills/omdrop")))
        self.assertIn("another omdrop made it", r.stdout)
        self.assertNotIn("systemctl --user daemon-reload", self.calls())

    def test_receiving_is_turned_off_before_removal(self):
        self.write(os.path.join(self.state, "active"), "")
        r = self.run_script("uninstall.sh")
        self.assertOk(r)
        calls = self.calls()
        self.assertLess(calls.index("omdrop off"), calls.index("sudo dnf remove --no-autoremove fdrop omdrop"))

    def test_dnf_refusing_leaves_home_alone(self):
        before = snapshot(self.home)
        r = self.run_script("uninstall.sh", MOCK_DNF_FAIL="1")
        self.assertFailed(r, "dnf did not remove")
        self.assertEqual(snapshot(self.home), before)

    def test_nothing_installed_still_cleans_generated_files(self):
        self.installed("omdrop-owl")
        r = self.run_script("uninstall.sh")
        self.assertOk(r)
        self.assertEqual(self.called("sudo"), [])
        self.assertFalse(os.path.exists(self.h(".config/systemd/user/airdrop-receiver.service")))

    def test_all_removes_settings_but_keeps_identity_and_backend(self):
        self.write(self.h(".config/omdrop/notes.txt"), "mine")
        # settings: yes; identity: a plain yes is not enough; omdrop-owl: no
        r = self.run_script("uninstall.sh", "--all", answers="y\ny\nn\n")
        self.assertOk(r)
        self.assertEqual(self.called("sudo"), ["sudo dnf remove --no-autoremove fdrop omdrop"])
        self.assertFalse(os.path.exists(self.h(".config/airdrop")))
        self.assertFalse(os.path.exists(self.h(".config/omdrop/settings")))
        self.assertFalse(os.path.exists(self.h(".config/systemd/user/airdrop-receiver.service.d")))
        # A file it does not know keeps its folder.
        self.assertEqual(self.read(self.h(".config/omdrop/notes.txt")), "mine")
        self.assertIn("still holds ~/.config/omdrop/notes.txt", r.stdout)
        for name in ("certificate.pem", "key.pem", "validation_record.cms"):
            self.assertTrue(os.path.exists(self.h(".omdrop/keys", name)))
        self.assertTrue(os.path.exists(self.h("Downloads/photo.jpg")))
        self.assertTrue(os.path.exists(self.h(".opendrop/keys/key.pem")))
        self.assertTrue(os.path.exists(self.h(".config/systemd/user/unrelated.service")))
        self.assertIn("omdrop-owl", self.installed_now())

    def test_all_lists_every_path_before_asking(self):
        r = self.run_script("uninstall.sh", "--all", answers="n\n")
        self.assertOk(r)
        self.assertIn("Nothing was changed", r.stdout)
        for p in ("~/.config/airdrop/config.toml", "~/.config/omdrop/known-senders",
                  "~/.config/systemd/user/airdrop-receiver.service.d/10-download-dir.conf",
                  "~/.omdrop/keys/key.pem", "~/.config/systemd/user/airdrop-receiver.service"):
            self.assertIn(p, r.stdout)
        self.assertEqual(self.called("sudo"), [])
        self.assertTrue(os.path.exists(self.h(".config/systemd/user/airdrop-receiver.service")))

    def test_all_deletes_identity_only_when_typed_out(self):
        self.write(self.h(".omdrop/keys/extra-unknown"), "x")
        r = self.run_script("uninstall.sh", "--all", answers="y\ndelete\nn\n")
        self.assertOk(r)
        self.assertFalse(os.path.exists(self.h(".omdrop/keys/key.pem")))
        self.assertTrue(os.path.exists(self.h(".omdrop/keys/extra-unknown")))

        os.remove(self.h(".omdrop/keys/extra-unknown"))
        self.write(self.h(".omdrop/keys/key.pem"), "secret")
        self.installed("omdrop-owl")
        r = self.run_script("uninstall.sh", "--all", answers="delete\nn\n")
        self.assertOk(r)
        self.assertFalse(os.path.exists(self.h(".omdrop")))

    def test_all_removes_omdrop_owl_only_when_approved(self):
        r = self.run_script("uninstall.sh", "--all", answers="y\nno\ny\n")
        self.assertOk(r)
        self.assertEqual(self.called("sudo"),
                         ["sudo dnf remove --no-autoremove fdrop omdrop omdrop-owl"])
        self.assertTrue(os.path.exists(self.h(".omdrop/keys/key.pem")))

    def test_all_removes_backups_install_made(self):
        backup = self.h(".local/share/fdrop/replaced-links/20261014-120000")
        os.makedirs(backup)
        os.symlink("/home/someone/omdrop-plugin/bin/omdrop", os.path.join(backup, "omdrop"))
        self.write(os.path.join(backup, "RESTORE"), "# Written by fdrop install.sh. To put these links back:\n")
        r = self.run_script("uninstall.sh", "--all", answers="y\nn\nn\n")
        self.assertOk(r)
        self.assertFalse(os.path.exists(self.h(".local/share/fdrop")))
        self.assertTrue(os.path.isdir(self.h(".local/share")))

    def test_dry_runs_change_nothing(self):
        self.write(os.path.join(self.state, "active"), "")
        for args in (["--dry-run"], ["--all", "--dry-run"]):
            open(self.log, "w").close()
            before = snapshot(self.home, self.state, self.tmpdir, self.repo)
            # Answers on stdin must not matter: a dry run reads none.
            r = self.run_script("uninstall.sh", *args, answers="y\ndelete\ny\ny\n")
            self.assertOk(r)
            self.assertEqual(snapshot(self.home, self.state, self.tmpdir, self.repo), before, args)
            self.assertNothingSystemWide()
            self.assertIn("would run: sudo dnf remove --no-autoremove fdrop omdrop", r.stdout)
            self.assertIn("would run: omdrop off", r.stdout)
            self.assertIn("rm -f -- " + self.h(".config/systemd/user/airdrop-receiver.service"), r.stdout)
            self.assertIn("would edit: ~/.config/mimeapps.list", r.stdout)
            self.assertIn("Nothing was changed", r.stdout)

        self.assertIn("if you answer yes to the settings question: rm -f -- "
                      + self.h(".config/airdrop/config.toml"), r.stdout)
        self.assertIn("if you answer yes to the identity question: rm -f -- "
                      + self.h(".omdrop/keys/key.pem"), r.stdout)
        self.assertIn("omdrop-owl is added to that dnf command", r.stdout)
        self.assertIn("only typing 'delete' confirms", r.stdout)


if __name__ == "__main__":
    unittest.main()
