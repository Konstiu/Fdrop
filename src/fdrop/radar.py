"""The radar: who is nearby, drawn after Omdrop's Radar.qml.

Three rings, a cross, a sweep that turns once every four seconds while
scanning, and a dot per device: nearer the centre the stronger its signal, at
a bearing fixed by its address (AWDL knows no direction). A dot flares as the
sweep passes it. Clicking one sends to it. The list beside the radar carries
the same devices for keyboard and screen-reader use, so this stays a picture.
"""
from __future__ import annotations

import math

import cairo  # noqa: F401  (registers the cairo types PyGObject draws with)
from gi.repository import GLib, GObject, Gtk, Pango, PangoCairo

from . import model

SWEEP_MS = 4000
FLARE_MS = 600
HIT_RADIUS = 14


class Radar(Gtk.DrawingArea):
    __gsignals__ = {
        "peer-clicked": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # A named device was swept: (mac, strength 0..1), for the sound.
        "peer-swept": (GObject.SignalFlags.RUN_FIRST, None, (str, float)),
    }

    def __init__(self):
        super().__init__()
        self.set_content_width(220)
        self.set_content_height(220)
        self.set_halign(Gtk.Align.CENTER)
        self.add_css_class("omdrop-radar")
        self.set_accessible_role(Gtk.AccessibleRole.IMG)
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Radar of nearby devices"])
        self.peers: list[model.Peer] = []
        self.scanning = False
        self._angle = 0.0
        self._start = None
        self._tick = 0
        self._flares: dict[str, int] = {}     # mac -> frame time of its last hit (µs)
        self._hover = ""
        self.set_draw_func(self._draw)

        click = Gtk.GestureClick()
        click.connect("released", self._clicked)
        self.add_controller(click)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._moved)
        motion.connect("leave", lambda *_: self._set_hover(""))
        self.add_controller(motion)
        self.connect("unmap", lambda *_: self._stop())
        self.connect("map", lambda *_: self._sync())

    # ------------------------------------------------------------ state

    def set_peers(self, peers: list[model.Peer]) -> None:
        self.peers = list(peers)
        present = {p.mac for p in self.peers}
        self._flares = {m: t for m, t in self._flares.items() if m in present}
        self.queue_draw()

    def set_scanning(self, scanning: bool) -> None:
        if scanning == self.scanning:
            return
        self.scanning = scanning
        self._sync()
        self.queue_draw()

    def _sync(self) -> None:
        if self.scanning and self.get_mapped():
            if not self._tick:
                self._start = None
                self._tick = self.add_tick_callback(self._on_tick)
        else:
            self._stop()

    def _stop(self) -> None:
        if self._tick:
            self.remove_tick_callback(self._tick)
            self._tick = 0
        self._flares.clear()

    def _on_tick(self, _widget, clock) -> bool:
        now = clock.get_frame_time()
        if self._start is None:
            self._start = now
        previous = self._angle
        self._angle = ((now - self._start) / 1000 % SWEEP_MS) / SWEEP_MS * 360
        travelled = (self._angle - previous) % 360
        if travelled:
            for p in self.peers:
                distance = (model.bearing(p.mac) - previous) % 360
                if 0 < distance <= travelled:
                    self._flares[p.mac] = now
                    if p.name:
                        self.emit("peer-swept", p.mac, model.signal_strength(p.rssi))
        self.queue_draw()
        return GLib.SOURCE_CONTINUE

    # --------------------------------------------------------- geometry

    def _geometry(self):
        w, h = self.get_width(), self.get_height()
        return w / 2, h / 2, max(0.0, min(w, h) / 2 - 8)

    def _position(self, peer: model.Peer):
        cx, cy, r = self._geometry()
        fraction = 0.92 - 0.74 * model.signal_strength(peer.rssi) if peer.rssi is not None else 0.96
        a = math.radians(model.bearing(peer.mac))
        return cx + math.sin(a) * r * fraction, cy - math.cos(a) * r * fraction

    def _peer_at(self, x, y) -> str:
        best, best_d = "", HIT_RADIUS
        for p in self.peers:
            px, py = self._position(p)
            d = math.hypot(px - x, py - y)
            if d <= best_d:
                best, best_d = p.mac, d
        return best

    def _clicked(self, _gesture, _n, x, y) -> None:
        mac = self._peer_at(x, y)
        if mac:
            self.emit("peer-clicked", mac)

    def _moved(self, _c, x, y) -> None:
        self._set_hover(self._peer_at(x, y))

    def _set_hover(self, mac: str) -> None:
        if mac != self._hover:
            self._hover = mac
            self.set_cursor_from_name("pointer" if mac else None)
            peer = next((p for p in self.peers if p.mac == mac), None)
            self.set_tooltip_text(f"Send to {peer.name or peer.mac}" if peer else None)
            self.queue_draw()

    # ---------------------------------------------------------- drawing

    def _draw(self, _area, cr, width, height) -> None:
        fg = self.get_color()
        cx, cy, r = self._geometry()
        if r <= 0:
            return

        def ink(alpha):
            cr.set_source_rgba(fg.red, fg.green, fg.blue, fg.alpha * alpha)

        cr.set_line_width(1)
        for i in (1, 2, 3):
            ink(0.16)
            cr.arc(cx, cy, r * i / 3, 0, 2 * math.pi)
            cr.stroke()
        ink(0.10)
        cr.move_to(cx - r, cy); cr.line_to(cx + r, cy)
        cr.move_to(cx, cy - r); cr.line_to(cx, cy + r)
        cr.stroke()

        if self.scanning:
            # The sweep's trailing glow: seventy degrees behind the leading edge.
            lead = math.radians(self._angle - 90)
            steps = 28
            for i in range(steps):
                a1 = lead - math.radians(70) * (i + 1) / steps
                a2 = lead - math.radians(70) * i / steps
                ink(0.25 * (1 - i / steps) ** 2)
                cr.move_to(cx, cy)
                cr.arc(cx, cy, r, a1, a2)
                cr.close_path()
                cr.fill()
            ink(1.0)
            cr.set_line_width(1.5)
            cr.move_to(cx, cy)
            cr.line_to(cx + math.cos(lead) * r, cy + math.sin(lead) * r)
            cr.stroke()

        clock = self.get_frame_clock()
        now = clock.get_frame_time() if clock else 0
        placed = []
        # Strongest first, so the nearest devices get first pick of label room.
        for p in sorted(self.peers, key=lambda p: (-model.signal_strength(p.rssi), p.mac)):
            x, y = self._position(p)
            flare = 0.0
            hit = self._flares.get(p.mac)
            if hit is not None:
                flare = max(0.0, 1 - (now - hit) / 1000 / FLARE_MS)
                flare = flare ** 3
            hot = p.mac == self._hover
            named = bool(p.name)
            size = (4.5 if named else 3.0) + 3 * flare + (1.5 if hot else 0)
            if flare > 0:
                ink(0.25 * flare)
                cr.arc(x, y, size + 6 * flare, 0, 2 * math.pi)
                cr.fill()
            ink(1.0 if named or hot else 0.55)
            cr.arc(x, y, size, 0, 2 * math.pi)
            cr.fill()
            if named:
                self._label(cr, p.name, x, y, cx, cy, r, placed, ink, hot)

    def _label(self, cr, text, x, y, cx, cy, r, placed, ink, hot) -> None:
        layout = self.create_pango_layout(text)
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        desc = self.get_pango_context().get_font_description().copy()
        desc.set_size(int(desc.get_size() * 0.82) or 9 * Pango.SCALE)
        if hot:
            desc.set_weight(Pango.Weight.BOLD)
        layout.set_font_description(desc)
        layout.set_width(int(r * 0.84 * Pango.SCALE))
        w, h = layout.get_pixel_size()
        gap = 9
        prefer = -1 if x > cx else 1
        for side, dy in ((prefer, 0), (-prefer, 0), (-prefer, h + 6), (-prefer, -h - 6)):
            left = x + gap if side > 0 else x - gap - w
            top = y - h / 2 + dy
            box = (left, top, w, h)
            inside = all(math.hypot(px - cx, py - cy) <= r
                         for px in (left, left + w) for py in (top, top + h))
            if not inside:
                continue
            if any(left - 4 < ox + ow + 4 and left + w + 4 > ox - 4
                   and top - 3 < oy + oh + 3 and top + h + 3 > oy - 3
                   for ox, oy, ow, oh in placed):
                continue
            placed.append(box)
            ink(1.0 if hot else 0.85)
            cr.move_to(left, top)
            PangoCairo.show_layout(cr, layout)
            return


__all__ = ["Radar"]
