"""Running the omdrop command without blocking the window.

The front end has no protocol code and no state of its own: every action is
one `omdrop …` process, as it is for the Omarchy panel. Everything here is
asynchronous on the GLib main loop.
"""
from __future__ import annotations

import os
import shutil
import signal

from gi.repository import Gio, GLib

FALLBACK_CLI = "/usr/share/omdrop/bin/omdrop"


def find_cli() -> str | None:
    """OMDROP_CLI, then PATH, then where the omdrop package installs it."""
    forced = os.environ.get("OMDROP_CLI")
    if forced:
        return forced
    return shutil.which("omdrop") or (FALLBACK_CLI if os.access(FALLBACK_CLI, os.X_OK) else None)


class Job:
    """One running command. `lines` jobs report stdout and stderr a line at a
    time; the rest report both whole when the command exits."""

    def __init__(self, proc: Gio.Subprocess):
        self.proc = proc
        self.running = True

    def terminate(self) -> None:
        # TERM, not KILL: `omdrop send` stops the sender's whole process group
        # on it, so nothing goes on knocking on the device's door afterwards.
        if self.running:
            self.proc.send_signal(signal.SIGTERM)


class Cli:
    def __init__(self, path: str | None = None):
        self.path = path or find_cli()

    @property
    def available(self) -> bool:
        return bool(self.path)

    def share_dir(self) -> str | None:
        """The share/ beside the real CLI, where its sounds are."""
        if not self.path:
            return None
        d = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(self.path))), "share")
        return d if os.path.isdir(d) else None

    def _spawn(self, argv, flags) -> Gio.Subprocess | None:
        try:
            return Gio.Subprocess.new(argv, flags)
        except GLib.Error:
            return None

    def run(self, args, done) -> Job | None:
        """Run to the end; done(returncode, stdout, stderr). A command that
        could not start at all answers (-1, "", reason)."""
        if not self.path:
            GLib.idle_add(lambda: done(-1, "", "The omdrop command is not installed.") and False)
            return None
        proc = self._spawn([self.path, *args],
                           Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE)
        if proc is None:
            GLib.idle_add(lambda: done(-1, "", f"{self.path} could not be run.") and False)
            return None
        job = Job(proc)

        def finished(p, res):
            job.running = False
            try:
                _ok, out, err = p.communicate_utf8_finish(res)
            except GLib.Error as e:
                done(-1, "", e.message)
                return
            code = p.get_exit_status() if p.get_if_exited() else -1
            done(code, out or "", err or "")

        proc.communicate_utf8_async(None, None, finished)
        return job

    def stream(self, args, on_out, on_err, done, argv0=None) -> Job | None:
        """Run, handing over each line as it is written; done(returncode)."""
        argv = [argv0 or self.path, *args]
        if not argv[0]:
            GLib.idle_add(lambda: done(-1) and False)
            return None
        proc = self._spawn(argv, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE)
        if proc is None:
            GLib.idle_add(lambda: done(-1) and False)
            return None
        job = Job(proc)
        _read_lines(proc.get_stdout_pipe(), on_out)
        _read_lines(proc.get_stderr_pipe(), on_err)

        def exited(p, res):
            job.running = False
            try:
                p.wait_finish(res)
            except GLib.Error:
                done(-1)
                return
            done(p.get_exit_status() if p.get_if_exited() else -1)

        proc.wait_async(None, exited)
        return job


def _read_lines(pipe, on_line) -> None:
    stream = Gio.DataInputStream.new(pipe)

    def got(s, res):
        try:
            line, _n = s.read_line_finish_utf8(res)
        except GLib.Error:
            return
        if line is None:        # end of stream
            return
        if on_line:
            on_line(line)
        s.read_line_async(GLib.PRIORITY_DEFAULT, None, got)

    stream.read_line_async(GLib.PRIORITY_DEFAULT, None, got)
