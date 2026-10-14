#!/bin/bash
# Build and install Fdrop and the omdrop package it drives, on Fedora.
#
#   ./install.sh            build both RPMs from source and install them
#   ./install.sh --dry-run  say what that would do, and change nothing
#   ./install.sh --help
#
# omdrop is built from the brentkearney/omdrop-plugin commit pinned in
# packaging/omdrop-revision, fetched into a temporary directory, by the same
# tools/build-rpm.sh a developer runs. omdrop-owl, the radio backend, must
# already be installed: this script never installs or replaces it. Nothing in
# your home directory is changed, except old ~/.local/bin links you agree to
# move aside.
#
# OMDROP_REPO=URL-or-path   fetch omdrop from there instead of GitHub
# OMDROP_SKIP_CHECK=1       skip omdrop's own test suite while building
set -uo pipefail
umask 022

ROOT=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=tools/install-common.sh
. "$ROOT/tools/install-common.sh"

REVISION_FILE=$ROOT/packaging/omdrop-revision
OMDROP_REPO=${OMDROP_REPO:-https://github.com/brentkearney/omdrop-plugin.git}

usage() {
  cat <<EOF
Usage: ./install.sh [--dry-run]

Builds the omdrop and fdrop RPMs from source and installs them with dnf.

  --dry-run   Change nothing: no build, no download, no dnf, no sudo. Print
              each command and path the install would touch.
  -h, --help  Show this help.

Needs Fedora, omdrop-owl already installed, and the build tools
(sudo dnf install git-core rpm-build desktop-file-utils appstream
python3-libarchive-c openssl). See docs/building.md.
EOF
}

for arg; do
  case $arg in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) usage; exit 0 ;;
    *) warn "Unknown option: $arg"; usage >&2; exit 2 ;;
  esac
done

# --- Checks: all of them, before anything happens ---------------------------

require_user
require_fedora

# command -> the Fedora package that has it
declare -A TOOL_PKG=(
  [git]=git-core [rpm]=rpm [rpmbuild]=rpm-build [dnf]=dnf [sudo]=sudo
  [tar]=tar [gzip]=gzip [python3]=python3
  [desktop-file-validate]=desktop-file-utils [appstreamcli]=appstream
)
[[ -z ${OMDROP_SKIP_CHECK:-} ]] && TOOL_PKG[openssl]=openssl
missing=()
for tool in "${!TOOL_PKG[@]}"; do
  command -v "$tool" >/dev/null 2>&1 || missing+=("${TOOL_PKG[$tool]}")
done
if [[ -z ${OMDROP_SKIP_CHECK:-} ]] && command -v python3 >/dev/null 2>&1 \
   && ! python3 -c 'import libarchive' >/dev/null 2>&1; then
  missing+=(python3-libarchive-c)
fi
if (( ${#missing[@]} )); then
  readarray -t missing < <(printf '%s\n' "${missing[@]}" | sort -u)
  die "Missing what the build needs. Install it first, then run this again:
  sudo dnf install ${missing[*]}"
fi

if ! pkg_installed omdrop-owl; then
  die "omdrop-owl is not installed. It is omdrop's radio backend
(https://github.com/t4t5/omdrop-owl) and is not built here: install it
first, then run this again. Nothing was changed."
fi

[[ -r $REVISION_FILE ]] || die "Cannot read $REVISION_FILE, which pins the omdrop commit."
revision=$(sed -e 's/#.*//' -e 's/[[:space:]]//g' "$REVISION_FILE" | grep -v '^$')
[[ $revision =~ ^[0-9a-f]{40}$ ]] \
  || die "$REVISION_FILE must hold one full 40-character commit SHA, and holds: ${revision:-nothing}"

# --- The plan ---------------------------------------------------------------

readarray -t old_links < <(legacy_links)
readarray -t old_files < <(legacy_files)

if (( DRY_RUN )); then
  say "Dry run: nothing is built, fetched, installed or changed."
fi
say "omdrop-owl: $(pkg_nevra omdrop-owl) is installed and stays as it is."
for p in omdrop fdrop omdrop-gnome; do
  pkg_installed "$p" && say "$p: $(pkg_nevra "$p") is installed now; dnf upgrades it to the new build, or keeps it if the build has the same version."
done
say "omdrop: commit $revision from $OMDROP_REPO"
say

# --- Fetch the pinned commit ------------------------------------------------

say "1. Fetch omdrop at the pinned commit, into a temporary directory"
if (( DRY_RUN )); then
  work="${TMPDIR:-/tmp}/fdrop-install.XXXXXX"
  say "  would create: $work (XXXXXX is random), removed again at the end"
else
  work=$(mktemp -d "${TMPDIR:-/tmp}/fdrop-install.XXXXXX") || die "Could not create a temporary directory."
  trap 'rm -rf -- "$work"' EXIT
fi
src=$work/omdrop-plugin
run git init -q "$src" || die "git could not create $src."
run git -C "$src" fetch -q --depth 1 --no-tags "$OMDROP_REPO" "$revision" \
  || die "Could not fetch omdrop commit $revision from $OMDROP_REPO. Check the network, and that the commit exists there. Nothing was installed."
run git -C "$src" -c advice.detachedHead=false checkout -q --detach FETCH_HEAD \
  || die "Could not check out omdrop commit $revision. Nothing was installed."
if (( DRY_RUN )); then
  say "  would check: the checkout is exactly $revision, or stop"
else
  got=$(git -C "$src" rev-parse HEAD)
  [[ $got == "$revision" ]] \
    || die "Fetched omdrop commit $got, not the pinned $revision. Nothing was installed."
  say "  omdrop is at $got, as pinned."
fi

# --- Build ------------------------------------------------------------------

say
say "2. Build both RPMs with tools/build-rpm.sh (as you, no root)"
rpms=$work/rpms
if ! run env RPM_OUT="$rpms" "$ROOT/tools/build-rpm.sh" "$src"; then
  die "Building the RPMs failed; the reason is above. Nothing was installed."
fi
if (( DRY_RUN )); then
  omdrop_rpm="$rpms/omdrop-VERSION-RELEASE.noarch.rpm"
  fdrop_rpm="$rpms/fdrop-VERSION-RELEASE.noarch.rpm"
  say "  The exact file names are known only after the build."
else
  readarray -t found < <(find "$rpms" -maxdepth 1 -name 'omdrop-[0-9]*.noarch.rpm')
  (( ${#found[@]} == 1 )) || die "The build did not leave exactly one omdrop RPM in $rpms. Nothing was installed."
  omdrop_rpm=${found[0]}
  readarray -t found < <(find "$rpms" -maxdepth 1 -name 'fdrop-[0-9]*.noarch.rpm')
  (( ${#found[@]} == 1 )) || die "The build did not leave exactly one fdrop RPM in $rpms. Nothing was installed."
  fdrop_rpm=${found[0]}
fi

# --- Install ----------------------------------------------------------------

say
say "3. Install both with dnf (dnf shows the transaction and asks first)"
if ! run sudo dnf install "$omdrop_rpm" "$fdrop_rpm"; then
  die "dnf did not install the packages; see above. Your home directory was not changed."
fi
if (( ! DRY_RUN )); then
  pkg_installed fdrop && pkg_installed omdrop \
    || die "dnf finished, but fdrop and omdrop are not both installed. Your home directory was not changed."
  pkg_installed omdrop-owl || die "omdrop-owl is no longer installed after dnf ran. Reinstall it."
fi

# --- Old links in ~/.local/bin ----------------------------------------------

# Shown only when there is something from an install by hand to deal with.
if [[ -n ${old_links[*]}${old_files[*]} ]]; then
  say
  say "4. Old links from installing by hand"
  for f in "${old_files[@]}"; do
    [[ -n $f ]] && say "  Left $(tildify "$f") alone: it is a file, not a link this script knows. It hides the packaged command while it is there."
  done
  if [[ -n ${old_links[*]} ]]; then
    say "  These come before /usr/bin on PATH and would hide the packaged commands:"
    for l in "${old_links[@]}"; do say "    $(tildify "$l") -> $(readlink "$l")"; done
    backup="$(replaced_links_dir)/$(date +%Y%m%d-%H%M%S)"
    if ask "Move them aside to $(tildify "$backup")? They can be moved back."; then
      run mkdir -p -- "$backup" || die "Could not create $backup."
      restore=()
      for l in "${old_links[@]}"; do
        run mv -T -- "$l" "$backup/${l##*/}" || die "Could not move $l. Nothing else was moved."
        restore+=("mv -T $(quote "$backup/${l##*/}") $(quote "$l")")
      done
      if (( DRY_RUN )); then
        say "  would write${COND}: $(tildify "$backup")/RESTORE, with the commands that move them back"
      else
        printf '# Written by fdrop install.sh. To put these links back:\n%s\n' "${restore[@]}" > "$backup/RESTORE"
        say "  Moved. To undo:"
        printf '    %s\n' "${restore[@]}"
      fi
    else
      say "  Left them in place. ~/.local/bin/omdrop keeps running the old copy."
    fi
    asked
  fi
fi

say
if (( DRY_RUN )); then
  say "Dry run finished. Nothing was changed."
else
  say "Installed $(pkg_nevra fdrop) and $(pkg_nevra omdrop)."
  say "Open Fdrop from the GNOME activities overview, or run: fdrop"
  say "Your identity (~/.omdrop), settings (~/.config/airdrop, ~/.config/omdrop) and ~/Downloads were not touched."
fi
