# Fdrop

A native GTK4/libadwaita window for [Omdrop](https://github.com/brentkearney/omdrop-plugin):
AirDrop between a Linux laptop and nearby Apple devices. It is a port of
Omdrop's Omarchy panel (`Panel.qml`, `Radar.qml`, `OmdropIcon.qml`) to Fedora
GNOME, and nothing more: the protocol, the receiver, the identity, the sender
and the radio all stay where they are upstream.

```
fdrop (this repository)    GTK4/libadwaita window; no state, no protocol code
        │  runs `omdrop …`, reads its JSON
omdrop (brentkearney/omdrop-plugin) the omdrop command, airdrop-serve.py, identity,
        │                           the per-user airdrop-receiver.service
        │  docs/radio-backend.md
omdrop-owl (t4t5/omdrop-owl)        omdrop-discoverable, send-to-peer, OWL: awdl0
```

## What maps to what

| Omarchy panel | Here | Through |
|---|---|---|
| Toggle switch, headline, detail line | Switch and headline at the top | `omdrop toggle ARG`, `omdrop status --json` |
| "Stay visible for" slider with countdown | "Stay visible for" row; "Time left" under it | `omdrop on ARG` reschedules a live window |
| Everyone / Contacts Only tabs | "Who can send" row | `omdrop visibility MODE` |
| Known-senders drawer | Known Senders dialog | `omdrop senders list --json / add / remove` |
| "They see you as" field | Entry row | `omdrop name NEW` |
| "Save files to" button (Nautilus via `pick.py`) | Row with the GTK folder chooser, plus "open folder" | `omdrop dir PATH` |
| "Send to peers" drawer, radar, peer rows | "Send to nearby devices": Scan button, device list, radar | `omdrop peers --json`, `omdrop peers --json -n --stream` |
| Click a peer, pick files (`send --pick`) | Click a device or its radar dot, or drop files on it; link button | `omdrop send --to MAC -- FILE…`, `omdrop send --to MAC URL` |
| Sliding progress segment, × to cancel | Pulsing progress bar, × to cancel (SIGTERM) or dismiss | the CLI stops the sender's process group |
| Toast on arrival, click to open | The CLI's own notification (`omdrop handle`), plus a "Received" list with Open and Show in Files | the receiver's journal, the same lines `omdrop watch` reads |
| "Not available" with doctor's sentence | Banner with the sentence; Diagnostics dialog | `omdrop doctor --json`, `omdrop doctor` |
| Radar sounds and mute | Same sounds through `pw-play`, mute button | `omdrop sound on/off` |
| Bar icon, Quickshell IPC, install-driver button | Not ported: Omarchy-specific | — |

Like the panel, the window polls the CLI and shows what it says, including its
refusals, verbatim. Scanning is opt-in, as opening the radar is upstream: the
name lookup connects to every peer and announces this machine over Bluetooth.

## Running

From a checkout, with `omdrop` on `PATH` (or `OMDROP_CLI=/path/to/omdrop`):

    ./fdrop

Shortcuts: Ctrl+D on/off, Ctrl+N scan, Ctrl+O send files, Ctrl+R or F5 refresh,
Ctrl+? shortcuts, Ctrl+Q quit.

Turning it on runs the backend's helper through `pkexec`; the window
itself never runs privileged. With omdrop-owl on an Intel card the radio is
exclusive: Wi-Fi is disconnected for as long as it is on.

## Packages

    sudo dnf install rpm-build desktop-file-utils appstream python3-libarchive-c
    tools/build-rpm.sh ~/omdrop-plugin
    sudo dnf install ./dist/*.noarch.rpm

Two packages, both built from source with nothing downloaded:

- `omdrop`: the omdrop command and receiver in `/usr/share/omdrop`, from the
  upstream commit checked out in `~/omdrop-plugin` plus
  `patches/omdrop-fedora.patch`. Requires `omdrop-owl`, and owns none of its
  files.
- `fdrop`: this window. Requires `omdrop`.

Removing them leaves everything in your home directory alone: `~/.omdrop`
(the AirDrop identity), `~/.config/airdrop` and the user service
`~/.config/systemd/user/airdrop-receiver.service`, which `omdrop on` rewrites
to the installed location the next time it runs.

## The Fedora patch to omdrop

`patches/omdrop-fedora.patch` is against `brentkearney/omdrop-plugin` and is
meant to go upstream. It changes `bin/omdrop` only, and nothing where pacman,
UFW and Omarchy's helpers exist:

- `vercmp` falls back to `sort -V` where pacman's is absent.
- `libkeyutils.so.1` is looked for the way `identity.py` loads it, not at
  Arch's path.
- `omdrop firewall install|remove` handles firewalld: one rich rule (IPv6
  link-local to link-local, TCP 8771) in the zone `awdl0` falls in, and
  nothing at all when that zone already admits the port, as Fedora
  Workstation's does.
- The receiver's packages are named for dnf when there is no pacman.
- Notifications fall back to `notify-send`, and links open with `xdg-open`.
- A copy installed under `/usr` does not link itself into `~/.local/bin`.
- Not Fedora-specific: `omdrop senders remove` could not remove the last
  sender on the list (`tests/test_senders.py`).

`tests/test_fedora.py` covers these with stubbed commands.

## Tests

    python3 -W ignore -m unittest discover -s tests

`tests/test_model.py` checks the wording and rules shared with the panel.
`tests/test_gui.py` opens the real window against `tests/fake_omdrop.py` (it
needs a display and skips without one): startup, backend detection and
recovery, on/off, the window ending, discovery, sending, cancelling and
receiving. Neither touches the radio, systemd or the firewall.

## Licence

MIT. The wording, the layout of the panel, the radar and the icon follow
Omdrop by Brent Kearney (MIT, `LICENSE.omdrop`).
