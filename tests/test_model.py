"""The wording and rules shared with Panel.qml, without GTK."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from fdrop import model  # noqa: E402

# What `omdrop status --json` printed on Fedora 44 with omdrop-owl 0.1.0, off.
REAL_OFF = ('{"receiving": false, "visible": false, "remaining": null, "name": "fedora", '
            '"dir": "/home/sam/Downloads", "mode": "off", "reason": "stopped", "rx_proven": false, '
            '"sound": true, "visibility": "everyone", "senders": 0}')


def status(**kw):
    base = json.loads(REAL_OFF)
    base.update(kw)
    return model.parse_status(json.dumps(base))


class StatusTests(unittest.TestCase):
    def test_real_status_output_is_usable_and_off(self):
        st = model.parse_status(REAL_OFF)
        self.assertTrue(st.usable)
        self.assertEqual(model.state_text(st), "Fdrop off")
        self.assertEqual(model.detail_text(st), "Nearby Apple devices cannot see this computer.")

    def test_garbage_or_no_answer_is_not_available(self):
        for text in ("", "not json", "[]", "null"):
            st = model.parse_status(text)
            self.assertFalse(st.usable, text)
            self.assertEqual(model.state_text(st), "Not available")

    def test_visible_null_means_the_radio_half_is_absent_not_off(self):
        st = status(visible=None)
        self.assertTrue(st.installed)
        self.assertFalse(st.usable)
        self.assertEqual(model.detail_text(st, doctor_text="Install omdrop-owl."), "Install omdrop-owl.")

    def test_receiving_but_not_visible_is_settling_then_a_fault(self):
        st = status(receiving=True, visible=False, mode="timed", remaining=600, reason="")
        self.assertEqual(model.state_text(st, settle_elapsed=3), "Waking the radio…")
        self.assertEqual(model.state_text(st, settle_elapsed=model.SETTLE_SECONDS), "Nobody can see you yet")
        self.assertIn("nothing is advertising", model.detail_text(st, settle_elapsed=model.SETTLE_SECONDS))

    def test_the_headline_follows_the_press_not_the_poll(self):
        # `omdrop on` starts the receiver before the radio: a poll mid-flight
        # reads receiving=true while the press is still turning on.
        st = status(receiving=True, visible=False)
        self.assertEqual(model.state_text(st, busy=True, turning_on=True), "Turning on…")
        self.assertEqual(model.state_text(st, busy=True, turning_on=False), "Turning off…")

    def test_on_names_who_can_see_this_computer(self):
        st = status(receiving=True, visible=True, mode="forever", reason="ok")
        self.assertEqual(model.state_text(st), "Fdrop on")
        self.assertIn("“fedora”", model.detail_text(st))
        self.assertFalse(model.detail_urgent(st))

    def test_contacts_only_with_an_empty_list_is_urgent(self):
        st = status(receiving=True, visible=True, visibility="contacts", senders=0)
        self.assertIn("everyone is refused", model.detail_text(st))
        self.assertTrue(model.detail_urgent(st))
        st = status(receiving=True, visible=True, visibility="contacts", senders=1)
        self.assertIn("1 known sender ", model.detail_text(st))

    def test_a_cli_refusal_is_shown_verbatim(self):
        st = status()
        self.assertEqual(model.detail_text(st, last_error="A send is running."), "A send is running.")

    def test_unknown_radio_reason_is_shown_only_when_something_is_wrong(self):
        self.assertIn("weird", model.detail_text(status(reason="weird")))
        self.assertNotIn("weird", model.detail_text(status(receiving=True, visible=True, reason="weird")))

    def test_window_row_reports_the_window_that_is_running(self):
        self.assertEqual(model.window_index_for(status(mode="once"), 3), 0)
        self.assertEqual(model.window_index_for(status(mode="forever"), 3), len(model.WINDOW_STOPS) - 1)
        # Ten minutes with four left reads as five.
        self.assertEqual(model.WINDOW_STOPS[model.window_index_for(status(mode="timed", remaining=240), 3)][0], "5m")
        self.assertEqual(model.window_index_for(status(mode="off"), 4), 4)

    def test_countdown_and_tilde(self):
        self.assertEqual(model.countdown(261), "04:21")
        self.assertEqual(model.tildify("/home/a/Downloads", "/home/a"), "~/Downloads")
        self.assertEqual(model.tildify("/home/ab/x", "/home/a"), "/home/ab/x")

    def test_doctor(self):
        d = model.parse_doctor('{"ready": false, "missing": [{"id": "radio", "say": "No radio."}]}')
        self.assertEqual(d.first, "No radio.")
        self.assertTrue(model.parse_doctor('{"ready": true, "missing": []}').ready)
        self.assertEqual(model.parse_doctor("junk").first, "")


class PeerTests(unittest.TestCase):
    LISTING = '[{"mac": "0A:bc:de:f0:12:34", "rssi": -48, "name": null}, {"mac": "0a:11:22:33:44:55", "rssi": null, "name": null}]'

    def test_listing_keeps_anonymous_peers(self):
        peers = model.parse_peers(self.LISTING)
        self.assertEqual([p.mac for p in peers], ["0a:bc:de:f0:12:34", "0a:11:22:33:44:55"])
        self.assertEqual(peers[1].title, "Unnamed device")
        self.assertIsNone(peers[1].rssi)

    def test_an_unreadable_listing_is_not_an_empty_room(self):
        self.assertIsNone(model.parse_peers("pkexec: not authorized"))
        self.assertEqual(model.parse_peers("[]"), [])

    def test_a_name_once_learnt_survives_the_next_bare_listing(self):
        book = model.PeerBook()
        book.merge(model.parse_peers(self.LISTING))
        self.assertTrue(book.learn(model.parse_peer_line('{"mac": "0a:bc:de:f0:12:34", "rssi": -48, "name": "iPhone"}')))
        self.assertFalse(book.learn(model.parse_peer_line('{"mac": "0a:bc:de:f0:12:34", "rssi": -48, "name": "iPhone"}')))
        book.merge(model.parse_peers(self.LISTING))
        self.assertEqual(book.peers[0].name, "iPhone")
        self.assertTrue(book.names_resolved)

    def test_the_device_being_sent_to_stays_listed(self):
        book = model.PeerBook()
        book.merge(model.parse_peers(self.LISTING))
        book.merge([])
        rows = book.rows("0a:bc:de:f0:12:34", "iPhone")
        self.assertEqual([(p.mac, p.name) for p in rows], [("0a:bc:de:f0:12:34", "iPhone")])

    def test_radar_status_lines(self):
        book = model.PeerBook()
        on = status(receiving=True, visible=True)
        self.assertEqual(model.radar_status(status(), False, book), "Turning Fdrop on…")
        self.assertEqual(model.radar_status(status(receiving=True), False, book), "Waiting for the radio…")
        self.assertEqual(model.radar_status(on, True, book), "Searching for devices…")
        book.merge(model.parse_peers(self.LISTING))
        self.assertEqual(model.radar_status(on, True, book), "Resolving device names…")
        book.names_resolved = True
        self.assertEqual(model.radar_status(on, True, book), "")

    def test_radar_placement_is_stable_and_bounded(self):
        self.assertEqual(model.bearing("0a:bc:de:f0:12:34"), model.bearing("0a:bc:de:f0:12:34"))
        self.assertIn(model.bearing("0a:11:22:33:44:55"), range(360))
        self.assertEqual(model.signal_strength(None), 0.0)
        self.assertEqual(model.signal_strength(-30), 1.0)
        self.assertEqual(model.signal_strength(-120), 0.0)


class SendTests(unittest.TestCase):
    def test_sender_lines_about_ports_and_paths_become_the_file_on_its_way(self):
        self.assertEqual(
            model.send_status_line("waiting up to 30s for 0a:bc:de:f0:12:34 to start listening on 8770", "photo.jpg", "iPhone"),
            "Sending photo.jpg to iPhone…")
        self.assertEqual(
            model.send_status_line("sending /home/k/a b.jpg to 0a:bc:de:f0:12:34 at [fe80::1]:8770", "", "iPhone"),
            "Sending a b.jpg to iPhone…")

    def test_narration_and_refusals_pass_through(self):
        self.assertEqual(model.send_status_line("They accepted. Sending...", "x", "y"), "They accepted. Sending...")
        self.assertEqual(model.send_status_line("omdrop: nobody is nearby", "x", "y"), "nobody is nearby")

    def test_describe_files_and_links(self):
        self.assertEqual(model.describe_files(["/a/photo.jpg"]), "photo.jpg")
        self.assertEqual(model.describe_files(["/a/1", "/a/2", "/a/3"]), "3 files")
        self.assertTrue(model.is_web_url("https://omarchy.org/"))
        for bad in ("", "omarchy.org", "file:///etc/passwd", "javascript:alert(1)", "https://", "https://a b"):
            self.assertFalse(model.is_web_url(bad), bad)


class ReceiverLogTests(unittest.TestCase):
    """Lines as airdrop-serve.py logs them (and as `omdrop watch` reads them)."""

    def test_stored_upload_names_every_file(self):
        a = model.parse_receiver_line("2026-10-13 18:30:01,000 INFO root: upload 1a2b: stored IMG_1.HEIC, IMG_2.HEIC", "/d")
        self.assertEqual((a.kind, a.files), ("files", ["/d/IMG_1.HEIC", "/d/IMG_2.HEIC"]))

    def test_absolute_names_are_kept(self):
        a = model.parse_receiver_line("INFO root: upload x: stored /tmp/a.txt", "/d")
        self.assertEqual(a.files, ["/tmp/a.txt"])

    def test_stored_link(self):
        a = model.parse_receiver_line("INFO root: link stored as omarchy.org.url -> https://omarchy.org/", "/d")
        self.assertEqual((a.kind, a.files), ("link", ["/d/omarchy.org.url"]))

    def test_ask_is_announced_and_other_lines_ignored(self):
        a = model.parse_receiver_line("INFO root: ask from Sam’s iPhone (iPhone15,2): type public.jpeg, 2 item(s): a, b", "/d")
        self.assertEqual((a.kind, a.text), ("ask", "Sam’s iPhone is sending 2 items…"))
        self.assertIsNone(model.parse_receiver_line("INFO root: connection from [fe80::1]:5000", "/d"))
        self.assertIsNone(model.parse_receiver_line("INFO root: upload x: 100 bytes dvzip (TotalBytes 100)", "/d"))


if __name__ == "__main__":
    unittest.main()
