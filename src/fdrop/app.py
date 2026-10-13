"""The application: one window, its shortcuts, and About."""
from __future__ import annotations

import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

from . import APP_ID, __version__  # noqa: E402


class FdropApp(Adw.Application):
    def __init__(self, cli=None):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.cli = cli
        self.window = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        # Run from a source checkout, the icon is beside the code.
        icons = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                             "data", "icons")
        display = Gdk.Display.get_default()
        if os.path.isdir(icons) and display:
            Gtk.IconTheme.get_for_display(display).add_search_path(icons)
        for name, fn in (("quit", lambda *_: self.quit()), ("about", self.about)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", fn)
            self.add_action(action)
        self.set_accels_for_action("app.quit", ["<Control>q"])
        self.set_accels_for_action("win.refresh", ["<Control>r", "F5"])
        self.set_accels_for_action("win.toggle", ["<Control>d"])
        self.set_accels_for_action("win.scan", ["<Control>n"])
        self.set_accels_for_action("win.send", ["<Control>o"])
        self.set_accels_for_action("win.shortcuts", ["<Control>question"])

    def do_activate(self):
        from .window import FdropWindow
        if not self.window:
            self.window = FdropWindow(self, self.cli)
        self.window.present()

    def about(self, *_a):
        Adw.AboutDialog(
            application_name="Fdrop",
            application_icon=APP_ID,
            version=__version__,
            comments="Send and receive files with nearby Apple devices over AirDrop.\n\n"
                     "A GNOME front end to the omdrop command by Brent Kearney, "
                     "with omdrop-owl as the radio backend.",
            website="https://github.com/brentkearney/omdrop-plugin",
            license_type=Gtk.License.MIT_X11,
            developers=["Brent Kearney (Omdrop)", "t4t5 (omdrop-owl)", "Konsti (GNOME front end)"],
        ).present(self.window)


def main(argv=None) -> int:
    return FdropApp().run(argv if argv is not None else sys.argv)
