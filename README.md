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

## Install

You need Fedora with [omdrop-owl](https://github.com/t4t5/omdrop-owl), the
radio backend, already installed. Then, from this checkout:

    sudo dnf install git-core rpm-build desktop-file-utils appstream python3-libarchive-c openssl
    ./install.sh --dry-run     # see what it would do; changes nothing
    ./install.sh

It fetches the omdrop commit pinned in `packaging/omdrop-revision`, builds the
`omdrop` and `fdrop` RPMs from source, and installs them with dnf. It never
installs or replaces omdrop-owl, and never touches your identity or settings.
If an older install by hand left `omdrop` or `fdrop` links in `~/.local/bin`,
it offers to move them aside and prints how to put them back.

## Use

Open **Fdrop** from the activities overview, or run `fdrop`.

Shortcuts: Ctrl+D on/off, Ctrl+N scan, Ctrl+O send files, Ctrl+R or F5 refresh,
Ctrl+? shortcuts, Ctrl+Q quit.

Turning it on runs the backend's helper through `pkexec`; the window
itself never runs privileged. With omdrop-owl on an Intel card the radio is
exclusive: Wi-Fi is disconnected for as long as it is on.

## Uninstall

    ./uninstall.sh --dry-run
    ./uninstall.sh

This removes the `fdrop` and `omdrop` packages, and the receiving service and
link handler that omdrop generated for them. It keeps `omdrop-owl`, your
AirDrop identity (`~/.omdrop`), your settings (`~/.config/airdrop`,
`~/.config/omdrop`) and `~/Downloads`.

## Complete cleanup

    ./uninstall.sh --all --dry-run
    ./uninstall.sh --all

This also removes omdrop's settings, after listing every path and asking.
Your identity and its private key are deleted only if you type `delete` at a
separate question, and omdrop-owl is removed only if you answer yes to its
own question. `~/Downloads` and `~/.opendrop` are never touched, and a folder
that still holds files the script does not recognise is left in place.

## Developing

Running from a checkout, building the RPMs by hand, the Fedora patch to
omdrop, and the tests: [docs/building.md](docs/building.md).

## Licence

MIT. The wording, the layout of the panel, the radar and the icon follow
Omdrop by Brent Kearney (MIT, `LICENSE.omdrop`).
