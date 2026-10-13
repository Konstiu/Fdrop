"""The window, driven for real against tests/fake_omdrop.py.

Needs a display (a Wayland or X session, or `xvfb-run`); skipped without one.
Every omdrop command the window runs goes to the fake, so nothing here touches
the radio, systemd or the firewall.
"""
import json
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gio, GLib, Gtk
    HAVE_DISPLAY = Gtk.init_check()
except Exception:  # no PyGObject, no GTK
    HAVE_DISPLAY = False

IPHONE = "0a:bc:de:f0:12:34"
ANON = "0a:11:22:33:44:55"


@unittest.skipUnless(HAVE_DISPLAY, "no display to open a window on")
class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fdrop.app import FdropApp
        cls.app = FdropApp()
        cls.app.set_application_id("io.github.konstiu.Fdrop.Test")
        cls.app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
        cls.app.register(None)

    def setUp(self):
        from fdrop.cli import Cli
        from fdrop.window import FdropWindow
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.makedirs(os.path.join(self.tmp.name, "Downloads"))
        os.environ["FAKE_OMDROP_STATE"] = self.tmp.name
        os.environ["FDROP_NO_JOURNAL"] = "1"
        self.cli = Cli(os.path.join(HERE, "fake_omdrop.py"))
        self.set_state()
        self.win = FdropWindow(self.app, self.cli)
        self.win.present()
        self.addCleanup(self.close)
        self.wait(lambda: self.win.st.installed, "the first status")

    def close(self):
        self.win.close()
        self.pump(0.05)

    # ---------------------------------------------------------- helpers

    def set_state(self, **kw):
        path = os.path.join(self.tmp.name, "state.json")
        try:
            with open(path) as f:
                state = json.load(f)
        except OSError:
            state = {}
        state.update(kw)
        with open(path, "w") as f:
            json.dump(state, f)

    def calls(self):
        try:
            with open(os.path.join(self.tmp.name, "calls")) as f:
                return [json.loads(line) for line in f]
        except OSError:
            return []

    def pump(self, seconds):
        end = time.monotonic() + seconds
        ctx = GLib.MainContext.default()
        while time.monotonic() < end:
            while ctx.pending():
                ctx.iteration(False)
            time.sleep(0.005)

    def wait(self, condition, what, timeout=8):
        end = time.monotonic() + timeout
        ctx = GLib.MainContext.default()
        while time.monotonic() < end:
            while ctx.pending():
                ctx.iteration(False)
            if condition():
                return
            time.sleep(0.005)
        self.fail(f"timed out waiting for {what}")

    def turn_on(self):
        self.win.toggle_omdrop()
        self.wait(lambda: not self.win.busy and self.win.st.receiving, "Fdrop to turn on")

    def scan(self):
        self.win.scan_button.set_active(True)
        self.wait(lambda: self.win.scanning and IPHONE in self.win.peer_rows, "the peer list")

    # ------------------------------------------------------------ tests

    def test_startup_shows_the_cli_state(self):
        win = self.win
        self.assertTrue(win.get_visible())
        self.assertEqual(win.state_label.get_label(), "Fdrop off")
        self.assertTrue(win.switch.get_visible())
        self.assertFalse(win.switch.get_active())
        self.assertFalse(win.banner.get_revealed())
        self.assertEqual(win.name_row.get_text(), "fedora")
        self.assertTrue(win.dir_row.get_subtitle().endswith("Downloads"))
        self.assertEqual(win.window_row.get_selected(), 3)      # 10 minutes

    def test_backend_missing_is_reported_with_doctors_sentence_and_recovers(self):
        self.set_state(usable=False)
        self.win.refresh_status()
        self.wait(lambda: self.win.banner.get_revealed() and self.win.doctor.first, "the banner")
        self.assertEqual(self.win.state_label.get_label(), "Not available")
        self.assertEqual(self.win.banner.get_title(), "No radio backend is installed.")
        self.assertFalse(self.win.switch.get_visible())
        self.assertFalse(self.win.receive_group.get_sensitive())
        # The backend coming back (a driver reload, Wi-Fi returning) clears it.
        self.set_state(usable=True)
        self.win.refresh_status()
        self.wait(lambda: not self.win.banner.get_revealed(), "the banner to go")
        self.assertEqual(self.win.state_label.get_label(), "Fdrop off")

    def test_cli_missing_is_said_plainly(self):
        self.cli.path = None
        self.win.refresh_status()
        self.wait(lambda: not self.win.st.installed, "status to fail")
        self.assertEqual(self.win.banner.get_title(), "The omdrop command is not installed.")

    def test_switch_turns_on_for_the_chosen_window_and_off_again(self):
        self.win.window_row.set_selected(2)                      # 5 minutes
        self.win.switch.activate()
        self.wait(lambda: self.win.st.receiving and not self.win.busy, "on")
        self.assertIn(["on", "5m"], self.calls())
        self.assertEqual(self.win.state_label.get_label(), "Fdrop on")
        self.assertTrue(self.win.switch.get_active())
        self.assertRegex(self.win.window_row.get_subtitle(), r"^Time left 0[45]:\d\d$")
        self.win.switch.activate()
        self.wait(lambda: not self.win.st.receiving and not self.win.busy, "off")
        self.assertIn(["off"], self.calls())
        self.assertFalse(self.win.switch.get_active())
        self.assertEqual(self.win.window_row.get_subtitle(), "")

    def test_a_refusal_is_shown_and_the_switch_stays_off(self):
        self.set_state(refuse_on="Wi-Fi isn't connected, and AirDrop shares its channel.")
        self.win.toggle_omdrop()
        self.wait(lambda: not self.win.busy and self.win.last_error, "the refusal")
        self.pump(0.2)
        self.assertEqual(self.win.detail_label.get_label(), "Wi-Fi isn't connected, and AirDrop shares its channel.")
        self.assertFalse(self.win.switch.get_active())
        self.assertEqual(self.win.state_label.get_label(), "Fdrop off")

    def test_choosing_a_duration_during_a_window_reschedules_it(self):
        self.turn_on()
        self.win.window_row.set_selected(5)                      # 30 minutes
        self.wait(lambda: ["on", "30m"] in self.calls(), "the reschedule")
        self.wait(lambda: (self.win.st.remaining or 0) > 1700, "the new deadline")
        self.assertEqual(self.win.window_row.get_selected(), 5)

    def test_window_timeout_turns_the_switch_off(self):
        self.turn_on()
        # One second left; the transient timer's `omdrop off` has run.
        self.win.st.remaining = 1
        self.set_state(receiving=False, visible=False, remaining=None, mode="off", reason="expired")
        self.wait(lambda: not self.win.st.receiving, "the window to end", timeout=5)
        self.pump(0.3)
        self.assertFalse(self.win.switch.get_active())
        self.assertEqual(self.win.state_label.get_label(), "Fdrop off")

    def test_a_window_ended_elsewhere_is_noticed_by_the_poll(self):
        self.turn_on()
        self.set_state(receiving=False, visible=False, remaining=None, mode="off")
        self.wait(lambda: not self.win.st.receiving, "the poll", timeout=6)
        self.assertFalse(self.win.switch.get_active())

    def test_scan_turns_omdrop_on_and_lists_named_and_anonymous_peers(self):
        self.win.scan_button.set_active(True)
        self.wait(lambda: self.win.scanning and len(self.win.peer_rows) == 2, "both peers")
        self.assertTrue(self.win.st.receiving)
        self.wait(lambda: self.win.peer_rows[IPHONE].title.get_label() == "Sam’s iPhone", "the name")
        self.assertEqual(self.win.peer_rows[ANON].title.get_label(), "Unnamed device")
        self.assertEqual(self.win.peer_rows[ANON].subtitle.get_label(), ANON)
        self.assertIn(["peers", "--json"], self.calls())
        self.assertIn(["peers", "--json", "-n", "--stream"], self.calls())
        self.assertEqual(len(self.win.radar.peers), 2)
        # Stopping the scan stops the lookups and clears the list.
        self.win.scan_button.set_active(False)
        self.wait(lambda: not self.win.peer_rows and self.win.names_job is None, "the scan to stop")

    def test_send_files_reports_progress_and_success(self):
        self.turn_on()
        self.scan()
        photo = os.path.join(self.tmp.name, "-photo.jpg")       # a name that begins with a dash
        open(photo, "w").close()
        self.win.send_files(IPHONE, [photo, photo])
        row = self.win.peer_rows[IPHONE]
        self.assertTrue(row.progress.get_visible())
        self.assertTrue(row.stop_button.get_visible())
        self.wait(lambda: self.win.send_done, "the send")
        self.assertIn(["send", "--to", IPHONE, "--", photo, photo], self.calls())
        self.pump(0.2)
        self.assertEqual(row.subtitle.get_label(), "Sent photo.jpg (2.4 MB in 10.5 s)")
        self.assertEqual(row.progress.get_fraction(), 1.0)
        self.assertFalse(row.stop_button.get_visible())

    def test_send_link(self):
        self.turn_on()
        self.scan()
        self.win._send(IPHONE, ["https://omarchy.org/"], "https://omarchy.org/")
        self.wait(lambda: self.win.send_done, "the link")
        self.assertIn(["send", "--to", IPHONE, "https://omarchy.org/"], self.calls())

    def test_a_declined_send_stays_on_the_row_until_dismissed(self):
        self.turn_on()
        self.scan()
        self.set_state(send="declined")
        self.win.send_files(IPHONE, ["/etc/hostname"])
        self.wait(lambda: self.win.send_failed, "the refusal")
        self.pump(0.2)
        row = self.win.peer_rows[IPHONE]
        self.assertEqual(row.subtitle.get_label(), "They declined it.")
        self.assertTrue(row.subtitle.has_css_class("error"))
        self.assertTrue(row.stop_button.get_visible())
        self.win.cancel_or_dismiss_send()
        self.assertEqual(self.win.sending_to, "")
        self.assertEqual(row.subtitle.get_label(), IPHONE)

    def test_cancel_terminates_the_send(self):
        self.turn_on()
        self.scan()
        self.set_state(send="hang")
        self.win.send_files(IPHONE, ["/etc/hostname"])
        self.wait(lambda: "Sending hostname" in self.win.send_status, "the send to start")
        started = time.monotonic()
        self.win.cancel_or_dismiss_send()
        self.wait(lambda: self.win.send_job is None, "the sender to exit")
        self.assertLess(time.monotonic() - started, 5)
        self.assertFalse(self.win.send_failed)
        self.assertEqual(self.win.sending_to, "")

    def test_only_one_send_at_a_time(self):
        self.turn_on()
        self.scan()
        self.set_state(send="hang")
        self.win.send_files(IPHONE, ["/etc/hostname"])
        self.win.send_files(ANON, ["/etc/hostname"])
        self.assertEqual(self.win.sending_to, IPHONE)
        self.wait(lambda: "Sending hostname" in self.win.send_status, "the send to start")
        self.assertEqual(sum(c[0] == "send" for c in self.calls()), 1)
        self.win.cancel_or_dismiss_send()
        self.wait(lambda: self.win.send_job is None, "the sender to exit")

    def test_received_files_and_links_are_listed(self):
        self.win.receiver_line("INFO root: ask from iPhone (iPhone15,2): type public.jpeg, 1 item(s): a")
        self.assertFalse(self.win.received_group.get_visible())
        self.win.receiver_line("INFO root: upload 1a: stored IMG_1.HEIC, IMG_2.HEIC")
        self.win.receiver_line("INFO root: link stored as omarchy.org.url -> https://omarchy.org/")
        self.assertTrue(self.win.received_group.get_visible())
        titles = []
        row = self.win.received_list.get_first_child()
        while row:
            titles.append(row.get_title())
            row = row.get_next_sibling()
        self.assertEqual(titles, ["omarchy.org.url", "IMG_2.HEIC", "IMG_1.HEIC"])

    def test_name_folder_audience_and_sound_go_through_the_cli(self):
        self.win.name_row.set_text("Konsti's Fedora")
        self.win.name_row.emit("apply")
        self.wait(lambda: self.win.st.name == "Konsti's Fedora", "the name")
        self.assertIn(["name", "Konsti's Fedora"], self.calls())

        self.win.audience_row.set_selected(1)
        self.wait(lambda: ["visibility", "contacts"] in self.calls(), "the audience")
        self.win.sound_button.set_active(False)
        self.wait(lambda: ["sound", "off"] in self.calls(), "the sound setting")
        self.wait(lambda: self.win.st.audience == "contacts" and not self.win.st.sound, "the poll")
        self.assertEqual(self.win.audience_row.get_selected(), 1)

    def test_a_refused_name_is_put_back(self):
        self.win.name_row.set_text("x" * 80)
        self.win.name_row.emit("apply")
        self.wait(lambda: self.win.last_error, "the refusal")
        self.assertEqual(self.win.name_row.get_text(), "fedora")
        self.assertIn("too long", self.win.detail_label.get_label())

    def test_dialogs_open(self):
        self.set_state(sender_list=["a@example.com"])
        self.win.show_senders()
        self.win.show_diagnostics()
        self.win.show_shortcuts()
        self.app.about()
        self.wait(lambda: ["senders", "list", "--json"] in self.calls() and ["doctor"] in self.calls(), "the dialogs")
        self.pump(0.3)


if __name__ == "__main__":
    unittest.main()
