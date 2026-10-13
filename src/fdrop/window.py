"""The Fdrop window: Omdrop's Panel.qml's functions in libadwaita widgets.

Like the panel it owns no settings. It polls `omdrop status --json`, runs one
`omdrop …` process per action, and shows what the CLI says, including its
refusals, verbatim.
"""
from __future__ import annotations

import os
import time

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from . import model
from .cli import Cli
from .radar import Radar

POLL_SECONDS = 3
PEERS_SECONDS = 2
RECEIVER_UNIT = "airdrop-receiver.service"


class PeerRow(Gtk.ListBoxRow):
    """A device that can be sent to: its name, its address, and the progress
    of a send while one is going to it. Activating it opens the file chooser;
    files dropped on it are sent."""

    def __init__(self, win: "FdropWindow", peer: model.Peer):
        super().__init__()
        self.win = win
        self.mac = peer.mac
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      margin_top=10, margin_bottom=10, margin_start=12, margin_end=12)
        top = Gtk.Box(spacing=8)
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True,
                         valign=Gtk.Align.CENTER)
        self.title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.subtitle = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.subtitle.add_css_class("caption")
        labels.append(self.title)
        labels.append(self.subtitle)
        top.append(labels)

        self.link_button = Gtk.Button(icon_name="insert-link-symbolic", valign=Gtk.Align.CENTER,
                                      tooltip_text="Send a link")
        self.link_button.add_css_class("flat")
        self.link_button.connect("clicked", lambda *_: win.ask_link(self.mac))
        top.append(self.link_button)
        self.stop_button = Gtk.Button(icon_name="window-close-symbolic", valign=Gtk.Align.CENTER)
        self.stop_button.add_css_class("flat")
        self.stop_button.connect("clicked", lambda *_: win.cancel_or_dismiss_send())
        top.append(self.stop_button)
        box.append(top)

        # Progress, honestly: the sender reports stages and no byte counts, so
        # the bar pulses while a transfer runs and fills when it has arrived.
        self.progress = Gtk.ProgressBar()
        box.append(self.progress)
        self.set_child(box)

        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self._dropped)
        self.add_controller(drop)
        self.update(peer)

    def _dropped(self, _target, value, _x, _y) -> bool:
        paths = [f.get_path() for f in value.get_files() if f.get_path()]
        if not paths or self.win.send_job:
            return False
        self.win.send_files(self.mac, paths)
        return True

    def update(self, peer: model.Peer) -> None:
        win = self.win
        target = win.sending_to == self.mac
        self.title.set_label(peer.title)
        if target:
            self.title.add_css_class("heading")
        else:
            self.title.remove_css_class("heading")
        status = win.send_status if target else ""
        self.subtitle.set_label(status or peer.mac)
        for cls, on in (("error", target and win.send_failed), ("dim-label", not status)):
            (self.subtitle.add_css_class if on else self.subtitle.remove_css_class)(cls)
        active = target and win.send_job is not None
        self.stop_button.set_visible(active or (target and win.send_failed))
        self.stop_button.set_tooltip_text("Dismiss" if win.send_failed else "Cancel the send")
        self.link_button.set_visible(win.send_job is None and not (target and win.send_failed))
        self.progress.set_visible(target and (active or win.send_done))
        if target and win.send_done:
            self.progress.set_fraction(1.0)
        what = f"Send files to {peer.name or peer.mac}"
        self.set_tooltip_text(None if win.send_job else what)
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             [f"{peer.title}, {status}" if status else what])


class FdropWindow(Adw.ApplicationWindow):
    def __init__(self, app, cli: Cli | None = None):
        super().__init__(application=app, title="Fdrop")
        self.set_default_size(460, 760)
        self.set_size_request(340, 360)
        self.cli = cli or Cli()

        self.st = model.Status()
        self.doctor = model.Doctor()
        self.asked_doctor = False
        self.last_error = ""
        self.busy = False
        self.turning_on = False
        self.settle_elapsed = 0
        self.window_index = model.DEFAULT_WINDOW_INDEX
        self.window_mode = ""
        self.pending_name = ""
        self.updating = False           # widgets being set from a poll, not by a hand

        self.status_job = None
        self.status_stale = False

        self.scan_wanted = False
        self.book = model.PeerBook()
        self.peer_rows: dict[str, PeerRow] = {}
        self.peers_job = None
        self.names_job = None
        self.names_started = 0.0
        self.names_retry = 0
        self.voices: list = []

        self.send_job = None
        self.sending_to = ""
        self.sending_name = ""
        self.sending_what = ""
        self.send_status = ""
        self.send_failed = False
        self.send_done = False
        self.send_cancelled = False
        self.send_clear = 0
        self.last_folder = None

        self.journal_job = None
        self.sources: list[int] = []

        self._build()
        self._actions()
        self.connect("close-request", self._closing)

        self.sources.append(GLib.timeout_add_seconds(POLL_SECONDS, self._poll))
        self.sources.append(GLib.timeout_add_seconds(1, self._second))
        self.sources.append(GLib.timeout_add_seconds(PEERS_SECONDS, self._peers_tick))
        self.sources.append(GLib.timeout_add(120, self._pulse))
        self.refresh_status()
        self._follow_receiver()
        self._render()

    # ------------------------------------------------------------- layout

    def _build(self) -> None:
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh (Ctrl+R)",
                             action_name="win.refresh")
        header.pack_start(refresh)
        menu = Gio.Menu()
        menu.append("Diagnostics", "win.diagnostics")
        menu.append("Keyboard Shortcuts", "win.shortcuts")
        menu.append("About Fdrop", "app.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                       tooltip_text="Main Menu", primary=True))
        view.add_top_bar(header)

        self.banner = Adw.Banner(button_label="Details")
        self.banner.connect("button-clicked", lambda *_: self.show_diagnostics())
        view.add_top_bar(self.banner)

        self.toasts = Adw.ToastOverlay()
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        clamp = Adw.Clamp(maximum_size=560, tightening_threshold=400)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24,
                       margin_top=18, margin_bottom=24, margin_start=12, margin_end=12)
        clamp.set_child(page)
        scroller.set_child(clamp)
        self.toasts.set_child(scroller)
        view.set_content(self.toasts)
        self.set_content(view)

        # The headline: the state of the thing the switch controls.
        hero = Gtk.Box(spacing=14)
        self.hero_icon = Gtk.Image(icon_name="io.github.konstiu.Fdrop-symbolic", pixel_size=40)
        self.hero_icon.set_accessible_role(Gtk.AccessibleRole.PRESENTATION)
        hero.append(self.hero_icon)
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True,
                         valign=Gtk.Align.CENTER)
        self.state_label = Gtk.Label(xalign=0, wrap=True)
        self.state_label.add_css_class("title-2")
        self.detail_label = Gtk.Label(xalign=0, wrap=True)
        labels.append(self.state_label)
        labels.append(self.detail_label)
        hero.append(labels)
        self.spinner = Adw.Spinner(valign=Gtk.Align.CENTER, visible=False)
        hero.append(self.spinner)
        self.switch = Gtk.Switch(valign=Gtk.Align.CENTER, tooltip_text="Receive from nearby Apple devices (Ctrl+D)")
        self.switch.update_property([Gtk.AccessibleProperty.LABEL], ["Fdrop"])
        self.switch.connect("state-set", self._switch_set)
        hero.append(self.switch)
        page.append(hero)

        # Receiving.
        self.receive_group = Adw.PreferencesGroup(title="Receiving")
        self.window_row = Adw.ComboRow(title="Stay visible for",
                                       model=Gtk.StringList.new([label for _arg, label in model.WINDOW_STOPS]))
        self.window_row.set_selected(self.window_index)
        self.window_row.connect("notify::selected", self._window_picked)
        self.receive_group.add(self.window_row)

        self.audience_row = Adw.ComboRow(title="Who can send",
                                         model=Gtk.StringList.new(["Everyone", "Contacts only"]))
        self.audience_row.connect("notify::selected", self._audience_picked)
        self.receive_group.add(self.audience_row)

        self.senders_row = Adw.ActionRow(title="Known senders", activatable=True,
                                         subtitle="Who may send in Contacts only")
        self.senders_row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        self.senders_row.connect("activated", lambda *_: self.show_senders())
        self.receive_group.add(self.senders_row)

        self.name_row = Adw.EntryRow(title="They see you as", show_apply_button=True)
        self.name_row.connect("apply", self._name_applied)
        self.receive_group.add(self.name_row)

        self.dir_row = Adw.ActionRow(title="Save files to", activatable=True,
                                     tooltip_text="Choose another folder")
        open_dir = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER,
                              tooltip_text="Open this folder")
        open_dir.add_css_class("flat")
        open_dir.connect("clicked", lambda *_: self._open_path(self.st.dir))
        self.dir_row.add_suffix(open_dir)
        self.dir_row.connect("activated", lambda *_: self.pick_download_dir())
        self.receive_group.add(self.dir_row)
        page.append(self.receive_group)

        # Sending: the devices that can be sent to, and the radar.
        self.nearby_group = Adw.PreferencesGroup(title="Send to nearby devices")
        tools = Gtk.Box(spacing=6)
        self.sound_button = Gtk.ToggleButton(icon_name="audio-volume-high-symbolic", valign=Gtk.Align.CENTER,
                                             tooltip_text="Radar sounds")
        self.sound_button.add_css_class("flat")
        self.sound_button.connect("toggled", self._sound_toggled)
        tools.append(self.sound_button)
        self.scan_button = Gtk.ToggleButton(label="Scan", valign=Gtk.Align.CENTER,
                                            tooltip_text="Look for nearby devices (Ctrl+N)")
        self.scan_button.connect("toggled", self._scan_toggled)
        tools.append(self.scan_button)
        self.nearby_group.set_header_suffix(tools)

        self.radar_revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        nearby = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.radar_status = Gtk.Label(xalign=0.5, wrap=True)
        self.radar_status.add_css_class("dim-label")
        nearby.append(self.radar_status)
        self.peer_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.peer_list.add_css_class("boxed-list")
        self.peer_list.connect("row-activated", lambda _l, row: self.send_to(row.mac))
        self.peer_list.update_property([Gtk.AccessibleProperty.LABEL], ["Nearby devices"])
        nearby.append(self.peer_list)
        self.radar = Radar()
        self.radar.connect("peer-clicked", lambda _r, mac: self.send_to(mac))
        self.radar.connect("peer-swept", self._swept)
        nearby.append(self.radar)
        self.radar_revealer.set_child(nearby)
        self.nearby_group.add(self.radar_revealer)
        page.append(self.nearby_group)

        # What has arrived while this window was open.
        self.received_group = Adw.PreferencesGroup(title="Received", visible=False)
        self.received_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.received_list.add_css_class("boxed-list")
        self.received_group.add(self.received_list)
        page.append(self.received_group)

    def _actions(self) -> None:
        for name, fn in (("refresh", self.refresh), ("toggle", self.toggle_omdrop),
                         ("scan", lambda: self.scan_button.set_active(not self.scan_button.get_active())),
                         ("send", self.send_to_only_peer), ("diagnostics", self.show_diagnostics),
                         ("shortcuts", self.show_shortcuts)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, fn=fn: fn())
            self.add_action(action)

    def _closing(self, *_a) -> bool:
        for s in self.sources:
            GLib.source_remove(s)
        self.sources = []
        # A send under way is the person's, and outlives a closed window only
        # by accident: stop it the way the cancel button does.
        for job in (self.send_job, self.names_job, self.peers_job, self.journal_job):
            if job:
                job.terminate()
        return False

    def toast(self, text: str, button: str | None = None, on_click=None) -> None:
        t = Adw.Toast(title=text, use_markup=False)
        if button and on_click:
            t.set_button_label(button)
            t.connect("button-clicked", lambda *_: on_click())
        self.toasts.add_toast(t)

    def notify(self, title: str, body: str) -> None:
        """A desktop notification, when the window is not the one in front."""
        if self.is_active():
            return
        n = Gio.Notification.new(title)
        n.set_body(body)
        self.get_application().send_notification(None, n)

    # ------------------------------------------------------------- status

    def refresh(self) -> None:
        self.refresh_status()
        if self.scanning:
            self._list_peers()

    def refresh_status(self) -> None:
        """Every change asks for a fresh status, and a request that arrives
        while a poll is running is queued, not dropped: the answer in flight
        predates the change, so it is discarded rather than shown."""
        if self.status_job:
            self.status_stale = True
            return
        self.status_job = self.cli.run(["status", "--json"], self._status_done) or True

    def _status_done(self, code, out, _err) -> None:
        self.status_job = None
        if self.status_stale:
            self.status_stale = False
            self.refresh_status()
            return
        self.apply_status(model.parse_status(out) if code == 0 else model.Status())

    def apply_status(self, st: model.Status) -> None:
        was_scanning = self.scanning
        self.st = st
        mode = st.mode
        if mode != self.window_mode:
            self.window_mode = mode
            self.window_index = model.window_index_for(st, self.window_index)
        if not (st.receiving and st.visible is not True):
            self.settle_elapsed = 0
        if not st.usable:
            # Asked once per transition into unusable, not on every poll.
            if not self.asked_doctor:
                self.asked_doctor = True
                self.cli.run(["doctor", "--json"], self._doctor_done)
        else:
            self.asked_doctor = False
            self.doctor = model.Doctor()
        self._scanning_changed(was_scanning)
        self._render()

    def _doctor_done(self, code, out, _err) -> None:
        if code == 0:
            self.doctor = model.parse_doctor(out)
            self._render()

    def _poll(self) -> bool:
        # A timed poll changes nothing, so one already in flight answers it.
        if not self.status_job:
            self.refresh_status()
        return GLib.SOURCE_CONTINUE

    def _second(self) -> bool:
        st = self.st
        if st.receiving and st.visible is not True:
            self.settle_elapsed += 1
        if st.receiving and st.remaining and st.remaining > 0:
            # The countdown ticks locally. Reaching zero is the moment the
            # window ends, so stop showing it as running and ask.
            st.remaining -= 1
            if st.remaining <= 0:
                st.remaining = None
                st.receiving = False
                st.visible = False
                self.refresh_status()
        self._render()
        return GLib.SOURCE_CONTINUE

    def _render(self) -> None:
        st = self.st
        self.updating = True
        try:
            settling = model.settling(st, self.settle_elapsed)
            self.state_label.set_label(model.state_text(st, self.busy, self.turning_on, self.settle_elapsed))
            doctor_text = self.doctor.first if self.cli.available else "The omdrop command is not installed."
            detail = model.detail_text(st, doctor_text, self.last_error, self.settle_elapsed)
            self.detail_label.set_label(detail)
            urgent = model.detail_urgent(st) or (bool(self.last_error) and detail == self.last_error)
            for cls, on in (("error", urgent), ("dim-label", not urgent)):
                (self.detail_label.add_css_class if on else self.detail_label.remove_css_class)(cls)
            self.hero_icon.set_opacity(1.0 if (self.busy or st.receiving) and st.usable else 0.45)
            self.spinner.set_visible(self.busy or settling)

            self.switch.set_visible(st.usable)
            self.switch.set_sensitive(not self.busy)
            shown = self.turning_on if self.busy else st.receiving
            if self.switch.get_active() != shown:
                self.switch.set_active(shown)
            self.switch.set_state(shown)

            self.banner.set_revealed(not st.usable)
            self.banner.set_title(detail if not st.usable else "")
            for group in (self.receive_group, self.nearby_group):
                group.set_sensitive(st.usable)

            if self.window_row.get_selected() != self.window_index:
                self.window_row.set_selected(self.window_index)
            counting = st.receiving and bool(st.remaining) and st.remaining > 0
            self.window_row.set_subtitle(f"Time left {model.countdown(st.remaining)}" if counting else "")

            audience = 1 if st.audience == "contacts" else 0
            if self.audience_row.get_selected() != audience:
                self.audience_row.set_selected(audience)
            n = st.senders
            self.senders_row.set_subtitle(f"{n} {'person' if n == 1 else 'people'} may send in Contacts only"
                                          if n else "Nobody yet; in Contacts only everyone is refused")

            # Don't fight the typing: the polled name is only taken back while
            # the field is not being edited.
            editing = bool(self.name_row.get_state_flags() & Gtk.StateFlags.FOCUS_WITHIN)
            shown_name = self.pending_name or st.name
            if not editing and self.name_row.get_text() != shown_name:
                self.name_row.set_text(shown_name)

            self.dir_row.set_subtitle(model.tildify(st.dir) if st.dir else "")

            if self.sound_button.get_active() != st.sound:
                self.sound_button.set_active(st.sound)
            self.sound_button.set_icon_name("audio-volume-high-symbolic" if st.sound
                                            else "audio-volume-muted-symbolic")
            self._render_nearby()
        finally:
            self.updating = False

    # ---------------------------------------------------- on, off, window

    def _switch_set(self, _switch, _state) -> bool:
        if self.updating:
            return False
        self.toggle_omdrop()
        return True          # the state follows the CLI, not the click

    def toggle_omdrop(self) -> None:
        if self.busy or not self.st.usable:
            return
        # Which way the press is going, captured now: `omdrop on` starts the
        # receiver before the radio, so a poll mid-flight would read as "on".
        self.turning_on = not self.st.receiving
        self.busy = True
        self.last_error = ""
        self._render()
        # `on` or `off` by name, not `toggle`: a press made on a stale status
        # must not turn off a window it meant to open.
        args = ["on", model.WINDOW_STOPS[self.window_index][0]] if self.turning_on else ["off"]
        self.cli.run(args, self._toggled)

    def _toggled(self, code, _out, err) -> None:
        self.busy = False
        # A refusal is the interesting case: the CLI declines rather than
        # pretending, and its sentence is what to show.
        self.last_error = "" if code == 0 else (err.strip() or "Fdrop could not be switched.")
        self.refresh_status()
        self._render()

    def _window_picked(self, row, _pspec) -> None:
        if self.updating:
            return
        self.window_index = row.get_selected()
        # Choosing a duration during a live window resets that window to it,
        # counting from now.
        st = self.st
        if not st.receiving or self.busy or not st.usable:
            return
        arg = model.WINDOW_STOPS[self.window_index][0]
        self.window_mode = model.window_mode(arg)
        self.last_error = ""
        self.cli.run(["on", arg], self._plain_done)

    def _plain_done(self, code, _out, err) -> None:
        self.last_error = "" if code == 0 else err.strip()
        self.refresh_status()
        self._render()

    def _audience_picked(self, row, _pspec) -> None:
        if self.updating:
            return
        mode = "contacts" if row.get_selected() == 1 else "everyone"
        if mode == self.st.audience:
            return
        self.st.audience = mode
        self.cli.run(["visibility", mode], self._plain_done)

    def _name_applied(self, row) -> None:
        name = row.get_text()
        if not name.strip() or name == self.st.name:
            return
        # The name is validated by the CLI and the receiver, not here.
        self.pending_name = name

        def done(code, _out, err):
            self.pending_name = ""
            self.last_error = "" if code == 0 else err.strip()
            if code != 0:
                row.set_text(self.st.name)
            self.refresh_status()
            self._render()

        self.cli.run(["name", name], done)

    def pick_download_dir(self) -> None:
        dialog = Gtk.FileDialog(title="Save Received Files To", modal=True)
        if self.st.dir and os.path.isdir(self.st.dir):
            dialog.set_initial_folder(Gio.File.new_for_path(self.st.dir))

        def chosen(d, res):
            try:
                folder = d.select_folder_finish(res)
            except GLib.Error:
                return          # closing the chooser is an answer, not an error
            if folder and folder.get_path():
                self.last_error = ""
                self.cli.run(["dir", folder.get_path()], self._plain_done)

        dialog.select_folder(self, None, chosen)

    def _sound_toggled(self, button) -> None:
        if self.updating:
            return
        on = button.get_active()
        self.st.sound = on
        self.cli.run(["sound", "on" if on else "off"], lambda *_: self.refresh_status())

    # ------------------------------------------------------------ nearby

    @property
    def scanning(self) -> bool:
        return self.scan_wanted and self.st.receiving and self.st.visible is True

    def _scan_toggled(self, button) -> None:
        was = self.scanning
        self.scan_wanted = button.get_active()
        # Scanning is what a window does, so asking for it on a machine that
        # is off opens one, for as long as the duration row says.
        if self.scan_wanted and self.st.usable and not self.st.receiving and not self.busy:
            self.toggle_omdrop()
        self._scanning_changed(was)
        self._render()

    def _scanning_changed(self, was: bool) -> None:
        now = self.scanning
        if now == was:
            return
        self.radar.set_scanning(now)
        if now:
            self._list_peers()
            self._start_names()
        else:
            # A radar that has been closed should not keep broadcasting.
            if self.names_job:
                self.names_job.terminate()
            if self.names_retry:
                GLib.source_remove(self.names_retry)
                self.names_retry = 0
            self.book.clear()

    def _peers_tick(self) -> bool:
        if self.scanning:
            self._list_peers()
        return GLib.SOURCE_CONTINUE

    def _list_peers(self) -> None:
        """The bare listing: passive and quick, polled for the dots."""
        if self.peers_job:
            return

        def done(code, out, _err):
            self.peers_job = None
            listing = model.parse_peers(out) if code == 0 else None
            if listing is not None and self.scanning:
                before = {p.mac for p in self.book.peers if p.name}
                self.book.merge(listing)
                self._blip_for_new(before)
                self._render()

        self.peers_job = self.cli.run(["peers", "--json"], done) or None

    def _start_names(self) -> None:
        """The named listing connects to every peer and takes half a minute,
        so it runs back to back and streams a line per peer as it answers."""
        self.names_retry = 0
        if self.names_job or not self.scanning:
            return GLib.SOURCE_REMOVE
        self.names_started = time.monotonic()

        def line(text):
            before = {p.mac for p in self.book.peers if p.name}
            if self.book.learn(model.parse_peer_line(text)):
                self._blip_for_new(before)
                self._render()

        def done(_code):
            self.names_job = None
            ran = time.monotonic() - self.names_started
            # A lookup that ran to its end has answered, names or not. One that
            # failed at once would otherwise restart in a tight loop.
            if ran >= 5:
                self.book.names_resolved = True
                self._render()
            if self.scanning:
                self.names_retry = GLib.timeout_add(250 if ran >= 5 else 10000, self._start_names)

        self.names_job = self.cli.stream(["peers", "--json", "-n", "--stream"], line, None, done)
        return GLib.SOURCE_REMOVE

    def _render_nearby(self) -> None:
        st = self.st
        self.radar_revealer.set_reveal_child(self.scan_wanted and st.usable)
        status = model.radar_status(st, self.scanning, self.book) if self.scan_wanted else ""
        self.radar_status.set_label(status)
        self.radar_status.set_visible(bool(status))
        self.nearby_group.set_description(
            "" if self.scan_wanted else "Scan to see who is nearby. Looking up names shows this computer to them.")

        rows = self.book.rows(self.sending_to, self.sending_name) if self.scan_wanted else []
        self.radar.set_peers(self.book.peers)
        want = {p.mac: p for p in rows}
        # Keyed by address: a row is created when its device arrives and
        # removed when it leaves, so a click never lands on a rebuilt row.
        for mac in [m for m in self.peer_rows if m not in want]:
            self.peer_list.remove(self.peer_rows.pop(mac))
        for p in rows:
            row = self.peer_rows.get(p.mac)
            if row is None:
                row = self.peer_rows[p.mac] = PeerRow(self, p)
                self.peer_list.append(row)
            else:
                row.update(p)
        self.peer_list.set_visible(bool(rows))

    def _blip_for_new(self, named_before: set) -> None:
        if any(p.name and p.mac not in named_before for p in self.book.peers):
            self._play("blip.wav", 0.55)

    def _swept(self, _radar, _mac, strength) -> None:
        self._play(f"contact-{1 + round(strength * 4)}.wav", 0.55 + 0.45 * strength)

    def _play(self, filename: str, volume: float) -> None:
        if not self.st.sound or not self.scanning:
            return
        share = self.cli.share_dir()
        path = os.path.join(share, "sounds", filename) if share else ""
        player = GLib.find_program_in_path("pw-play")
        self.voices = [v for v in self.voices if v.get_identifier()]
        if not player or not os.path.isfile(path) or len(self.voices) >= 3:
            return
        try:
            self.voices.append(Gio.Subprocess.new([player, "--volume", f"{volume:.2f}", path],
                                                  Gio.SubprocessFlags.STDERR_SILENCE))
            self.voices[-1].wait_async(None, lambda p, r: p.wait_finish(r))
        except GLib.Error:
            pass

    # ----------------------------------------------------------- sending

    def _who(self, mac: str) -> str:
        return self.book.names.get(mac) or self.sending_name or mac

    def send_to_only_peer(self) -> None:
        rows = self.book.rows() if self.scan_wanted else []
        if len(rows) == 1:
            self.send_to(rows[0].mac)
        elif not rows:
            self.toast("No nearby device to send to yet. Press Scan first.")
        else:
            self.toast("Several devices are nearby. Choose one from the list.")

    def send_to(self, mac: str) -> None:
        if self.send_job:
            return
        dialog = Gtk.FileDialog(title=f"Send to {self.book.names.get(mac) or 'a nearby device'}", modal=True)
        if self.last_folder:
            dialog.set_initial_folder(self.last_folder)

        def chosen(d, res):
            try:
                files = d.open_multiple_finish(res)
            except GLib.Error:
                return
            paths = [f.get_path() for f in files if f.get_path()]
            if paths:
                self.last_folder = Gio.File.new_for_path(os.path.dirname(paths[0]))
                self.send_files(mac, paths)

        dialog.open_multiple(self, None, chosen)

    def ask_link(self, mac: str) -> None:
        if self.send_job:
            return
        dialog = Adw.AlertDialog(heading="Send a Link", body=f"To {self.book.names.get(mac) or mac}")
        entry = Gtk.Entry(placeholder_text="https://…", activates_default=True,
                          input_purpose=Gtk.InputPurpose.URL)
        entry.update_property([Gtk.AccessibleProperty.LABEL], ["Web address"])
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("send", "Send")
        dialog.set_response_appearance("send", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("send")
        dialog.set_close_response("cancel")
        dialog.set_response_enabled("send", False)
        entry.connect("changed", lambda e: dialog.set_response_enabled("send", model.is_web_url(e.get_text())))

        def answered(_d, response):
            url = entry.get_text().strip()
            if response == "send" and model.is_web_url(url):
                self._send(mac, [url], url)

        dialog.connect("response", answered)
        dialog.present(self)

    def send_files(self, mac: str, paths: list[str]) -> None:
        # `--` because a filename may begin with a dash.
        self._send(mac, ["--", *paths], model.describe_files(paths))

    def _send(self, mac: str, payload: list[str], what: str) -> None:
        if self.send_job:
            return
        if self.send_clear:
            GLib.source_remove(self.send_clear)
            self.send_clear = 0
        self.sending_to = mac
        self.sending_name = self.book.names.get(mac, "")
        self.sending_what = what
        self.send_status = f"Preparing {what}…"
        self.send_failed = self.send_done = self.send_cancelled = False

        def line(text):
            text = text.strip()
            if text and not self.send_cancelled:
                self.send_status = model.send_status_line(text, self.sending_what, self._who(mac))
                self._render()

        self.send_job = self.cli.stream(["send", "--to", mac, *payload], line, line, self._send_exited)
        self._render()

    def cancel_or_dismiss_send(self) -> None:
        if self.send_job:
            self.send_cancelled = True
            self.send_status = "Cancelling…"
            self.send_job.terminate()
        elif self.send_failed:
            self._clear_send()
        self._render()

    def _clear_send(self) -> bool:
        self.send_clear = 0
        self.sending_to = self.send_status = ""
        self.send_failed = self.send_done = False
        self._render()
        return GLib.SOURCE_REMOVE

    def _send_exited(self, code) -> None:
        self.send_job = None
        who, what = self._who(self.sending_to), self.sending_what
        if self.send_cancelled:
            # A send the person cancelled is not a failure; the row just goes
            # back to being a row.
            self.send_cancelled = False
            self._clear_send()
            self.toast("Send cancelled")
            return
        self.send_done = code == 0
        self.send_failed = code != 0
        if self.send_done:
            self.toast(f"Sent {what} to {who}")
            self.notify("Sent", f"{what} to {who}")
            # Long enough to read, then out of the way. A failure stays on the
            # row until it has been read and dismissed.
            self.send_clear = GLib.timeout_add(2500, self._clear_send)
        else:
            if not self.send_status or self.send_status.startswith(("Preparing", "Sending")):
                self.send_status = "The transfer failed."
            self.toast(f"Could not send {what} to {who}")
            self.notify("Send failed", self.send_status)
        self._render()

    def _pulse(self) -> bool:
        if self.send_job:
            row = self.peer_rows.get(self.sending_to)
            if row:
                row.progress.pulse()
        return GLib.SOURCE_CONTINUE

    # --------------------------------------------------------- receiving

    def _follow_receiver(self) -> None:
        """Follow the receiver's own log, as `omdrop watch` does: it names
        the files it stored. The desktop notification for each arrival is the
        watcher's (`omdrop handle`), so this only lists them in the window."""
        journalctl = GLib.find_program_in_path("journalctl")
        if not journalctl or os.environ.get("FDROP_NO_JOURNAL"):
            return
        self.journal_job = self.cli.stream(
            ["--user", "-u", RECEIVER_UNIT, "-f", "-n", "0", "-o", "cat"],
            self.receiver_line, None, lambda _code: setattr(self, "journal_job", None),
            argv0=journalctl)

    def receiver_line(self, line: str) -> None:
        arrival = model.parse_receiver_line(line, self.st.dir or os.path.expanduser("~/Downloads"))
        if not arrival:
            return
        if arrival.kind == "ask":
            self.toast(arrival.text)
            return
        for path in arrival.files:
            self._add_received(path)
        name = os.path.basename(arrival.files[0]) if len(arrival.files) == 1 else f"{len(arrival.files)} files"
        first = arrival.files[0]
        self.toast(f"Received {name}", "Open", lambda: self._open_received(first))

    def _add_received(self, path: str) -> None:
        row = Adw.ActionRow(title=GLib.markup_escape_text(os.path.basename(path)),
                            subtitle=GLib.markup_escape_text(model.tildify(os.path.dirname(path))),
                            activatable=True, tooltip_text="Open")
        row.connect("activated", lambda *_: self._open_received(path))
        show = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER,
                          tooltip_text="Show in Files")
        show.add_css_class("flat")
        show.connect("clicked", lambda *_: self._show_in_folder(path))
        row.add_suffix(show)
        self.received_list.prepend(row)
        self.received_group.set_visible(True)

    def _open_received(self, path: str) -> None:
        # A received link goes through the CLI, which opens web links only.
        if path.lower().endswith((".url", ".webloc")):
            self.cli.run(["open-link", path], lambda code, _o, err: code and self.toast(err.strip() or "That link could not be opened."))
        else:
            self._open_path(path)

    def _open_path(self, path: str) -> None:
        if not path or not os.path.exists(path):
            self.toast(f"{model.tildify(path)} is not there any more." if path else "Nothing to open.")
            return
        Gtk.FileLauncher.new(Gio.File.new_for_path(path)).launch(self, None, self._launched)

    def _show_in_folder(self, path: str) -> None:
        if not os.path.exists(path):
            self._open_path(os.path.dirname(path))
            return
        Gtk.FileLauncher.new(Gio.File.new_for_path(path)).open_containing_folder(self, None, self._launched)

    def _launched(self, launcher, res) -> None:
        try:
            launcher.launch_finish(res)
        except GLib.Error as e:
            if not e.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED):
                self.toast(e.message)

    # ----------------------------------------------------- known senders

    def show_senders(self) -> None:
        dialog = Adw.Dialog(title="Known Senders", content_width=420, content_height=460)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(
            description="In Contacts only, only these people can send to you. "
                        "Use the email or phone number of their Apple ID.")
        entry = Adw.EntryRow(title="Apple ID email or phone number", show_apply_button=True)
        group.add(entry)
        error = Gtk.Label(xalign=0, wrap=True, visible=False, margin_top=6)
        error.add_css_class("error")
        group.add(error)
        page.add(group)
        people = Adw.PreferencesGroup(title="May send")
        page.add(people)
        view.set_content(page)
        dialog.set_child(view)
        rows: list = []

        def listed(code, out, _err):
            try:
                import json
                # Whatever the exit status: an empty list exits non-zero.
                ids = [str(i) for i in json.loads(out)]
            except (ValueError, TypeError):
                ids = []
            for r in rows:
                people.remove(r)
            rows.clear()
            for ident in ids:
                row = Adw.ActionRow(title=GLib.markup_escape_text(ident))
                remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                                    tooltip_text=f"Remove {ident}")
                remove.add_css_class("flat")
                remove.connect("clicked", lambda _b, ident=ident: edit("remove", ident, False))
                row.add_suffix(remove)
                people.add(row)
                rows.append(row)
            people.set_visible(bool(ids))

        def reload():
            self.cli.run(["senders", "list", "--json"], listed)

        def edit(verb, ident, clear):
            def done(code, _out, err):
                # The CLI validates, so its sentence is shown rather than a
                # second copy of the rules kept here.
                message = "" if code == 0 else err.strip().removeprefix("omdrop: ")
                error.set_label(message)
                error.set_visible(bool(message))
                if code == 0 and clear:
                    entry.set_text("")
                reload()
                self.refresh_status()

            self.cli.run(["senders", verb, ident], done)

        entry.connect("apply", lambda e: e.get_text().strip() and edit("add", e.get_text().strip(), True))
        reload()
        dialog.present(self)

    # ------------------------------------------------- health and help

    def show_diagnostics(self) -> None:
        dialog = Adw.Dialog(title="Diagnostics", content_width=520, content_height=420)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        text = Gtk.Label(xalign=0, yalign=0, wrap=True, selectable=True, label="Asking omdrop…",
                         margin_top=12, margin_bottom=12, margin_start=18, margin_end=18)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(text)
        view.set_content(scroller)
        dialog.set_child(view)
        parts = {"path": f"Command: {self.cli.path or 'not found'}"}

        def show():
            text.set_label("\n\n".join(parts[k] for k in ("doctor", "version", "path") if k in parts))

        def got(key, prefix=""):
            def done(code, out, err):
                parts[key] = prefix + ((out if code == 0 else err or out).strip() or "No answer.")
                show()
            return done

        self.cli.run(["doctor"], got("doctor"))
        self.cli.run(["version"], got("version", "omdrop "))
        dialog.present(self)

    def show_shortcuts(self) -> None:
        if not hasattr(Adw, "ShortcutsDialog"):
            self.toast("Ctrl+D on/off · Ctrl+N scan · Ctrl+O send · Ctrl+R refresh · Ctrl+Q quit")
            return
        dialog = Adw.ShortcutsDialog()
        section = Adw.ShortcutsSection(title="Fdrop")
        for title, accel in (("Turn Fdrop on or off", "<Control>d"), ("Scan for nearby devices", "<Control>n"),
                             ("Send files", "<Control>o"), ("Refresh", "<Control>r"),
                             ("Keyboard shortcuts", "<Control>question"), ("Quit", "<Control>q")):
            section.add(Adw.ShortcutsItem.new(title, accel))
        dialog.add(section)
        dialog.present(self)
