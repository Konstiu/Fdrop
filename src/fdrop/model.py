"""What the window shows, worked out from what the omdrop CLI says.

Nothing here imports GTK and nothing here keeps settings: `omdrop status
--json` is the single source of truth, exactly as it is for the Omarchy panel
(Panel.qml), whose wording and rules this file follows so both front ends
describe the same state in the same words.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

# The panel's slider stops. Both ends are modes, not durations.
WINDOW_STOPS = [
    ("once", "One file, then off"),
    ("1m", "1 minute"),
    ("5m", "5 minutes"),
    ("10m", "10 minutes"),
    ("15m", "15 minutes"),
    ("30m", "30 minutes"),
    ("60m", "1 hour"),
    ("forever", "Until I turn it off"),
]
DEFAULT_WINDOW_INDEX = 3
# The radio comes up some seconds after the receiver; until then "not visible"
# is a start in progress, not a fault.
SETTLE_SECONDS = 200


@dataclass
class Status:
    installed: bool = False        # the CLI answered at all
    receiving: bool = False
    visible: bool | None = None    # None: the radio half is absent, we cannot tell
    remaining: int | None = None
    name: str = ""
    dir: str = ""
    mode: str = "off"
    reason: str = ""
    sound: bool = True
    audience: str = "everyone"
    senders: int = 0

    @property
    def usable(self) -> bool:
        return self.installed and self.visible is not None


def parse_status(text: str) -> Status:
    """`omdrop status --json`. Anything unreadable is "not installed"."""
    try:
        s = json.loads(text)
        if not isinstance(s, dict):
            raise ValueError
    except (ValueError, TypeError):
        return Status()
    remaining = s.get("remaining")
    visible = s.get("visible")
    return Status(
        installed=True,
        receiving=bool(s.get("receiving")),
        visible=visible if isinstance(visible, bool) else None,
        remaining=remaining if isinstance(remaining, int) and not isinstance(remaining, bool) else None,
        name=s.get("name") or "",
        dir=s.get("dir") or "",
        mode=s.get("mode") or "off",
        reason=s.get("reason") or "",
        sound=s.get("sound") is not False,
        audience="contacts" if s.get("visibility") == "contacts" else "everyone",
        senders=s.get("senders") if isinstance(s.get("senders"), int) else 0,
    )


@dataclass
class Doctor:
    ready: bool = True
    missing: list = field(default_factory=list)   # [(id, sentence)]

    @property
    def first(self) -> str:
        return self.missing[0][1] if self.missing else ""


def parse_doctor(text: str) -> Doctor:
    try:
        d = json.loads(text)
        rows = [(str(m["id"]), str(m["say"])) for m in d.get("missing") or []]
    except (ValueError, TypeError, KeyError, AttributeError):
        return Doctor()
    return Doctor(ready=not rows, missing=rows)


def tildify(path: str, home: str | None = None) -> str:
    home = home if home is not None else os.path.expanduser("~")
    if home and (path == home or path.startswith(home + "/")):
        return "~" + path[len(home):]
    return path


def reason_text(st: Status) -> str:
    if st.reason == "no_awdl0":
        return "The AWDL interface is missing. The Wi-Fi driver needs loading."
    if st.reason in ("parked", "ok", "expired", "stopped", ""):
        return ""
    return "" if st.visible else "The radio side reports: " + st.reason


def settling(st: Status, settle_elapsed: int) -> bool:
    return st.receiving and st.visible is not True and settle_elapsed < SETTLE_SECONDS


def state_text(st: Status, busy: bool = False, turning_on: bool = False, settle_elapsed: int = 0) -> str:
    if not st.usable:
        return "Not available"
    if busy:
        return "Turning on…" if turning_on else "Turning off…"
    if settling(st, settle_elapsed):
        return "Waking the radio…"
    if not st.receiving:
        return "Fdrop off"
    if st.visible is False:
        return "Nobody can see you yet"
    return "Fdrop on"


def visible_text(st: Status) -> str:
    who = f"“{st.name}”" if st.name else "this computer"
    if st.audience == "contacts":
        if st.senders == 0:
            return "Your known-senders list is empty, so everyone is refused. Add people under Known senders."
        n = f"{st.senders} known sender" + ("" if st.senders == 1 else "s")
        return f"Only your {n} should see {who} in AirDrop, after 10 seconds."
    return f"Nearby Apple devices should see {who} in AirDrop, after 10 seconds."


def detail_text(st: Status, doctor_text: str = "", last_error: str = "", settle_elapsed: int = 0) -> str:
    if not st.usable:
        return doctor_text or "Needs the omdrop command and a radio backend such as omdrop-owl."
    if settling(st, settle_elapsed):
        return "The radio takes a few seconds to come up."
    if last_error:
        return last_error
    if reason_text(st):
        return reason_text(st)
    if not st.receiving:
        return "Nearby Apple devices cannot see this computer."
    if st.visible is False:
        return "The receiver is up, but nothing is advertising on the radio."
    return visible_text(st)


def detail_urgent(st: Status) -> bool:
    return st.usable and st.receiving and st.visible is True and st.audience == "contacts" and st.senders == 0


def countdown(seconds: int) -> str:
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def window_index_for(st: Status, current: int) -> int:
    """Where the duration row should sit for the window that is running. Only
    what is left is knowable, so a timed window lands on the shortest stop that
    could still be running: ten minutes with four left reads as five."""
    if st.mode == "once":
        return 0
    if st.mode == "forever":
        return len(WINDOW_STOPS) - 1
    if st.mode == "timed" and st.remaining and st.remaining > 0:
        for i in range(1, len(WINDOW_STOPS) - 1):
            if int(WINDOW_STOPS[i][0][:-1]) * 60 >= st.remaining:
                return i
    return current


def window_mode(arg: str) -> str:
    return arg if arg in ("once", "forever") else "timed"


# ------------------------------------------------------------------- peers

@dataclass
class Peer:
    mac: str
    rssi: int | None = None
    name: str = ""

    @property
    def title(self) -> str:
        return self.name or "Unnamed device"


def parse_peers(text: str) -> list[Peer] | None:
    """`omdrop peers --json`: one array. None when it could not be read, which
    is not the same as an empty room."""
    try:
        rows = json.loads(text)
        if not isinstance(rows, list):
            return None
    except (ValueError, TypeError):
        return None
    return [p for p in map(_peer, rows) if p]


def parse_peer_line(line: str) -> Peer | None:
    """One line of `omdrop peers --json -n --stream`."""
    try:
        return _peer(json.loads(line))
    except (ValueError, TypeError):
        return None


def _peer(row) -> Peer | None:
    if not isinstance(row, dict) or not isinstance(row.get("mac"), str) or not row["mac"]:
        return None
    rssi = row.get("rssi")
    name = row.get("name")
    return Peer(row["mac"].lower(),
                rssi if isinstance(rssi, (int, float)) and not isinstance(rssi, bool) else None,
                name if isinstance(name, str) else "")


class PeerBook:
    """The last listing, with every name learnt this session merged in. A
    device answers a name lookup only while its AirDrop is listening, so a
    name once heard is kept rather than lost between lookups."""

    def __init__(self):
        self.peers: list[Peer] = []
        self.names: dict[str, str] = {}
        self.names_resolved = False

    def merge(self, listing: list[Peer]) -> None:
        seen, out = set(), []
        for p in listing:
            if p.mac in seen:
                continue
            seen.add(p.mac)
            if p.name:
                self.names[p.mac] = p.name
            out.append(Peer(p.mac, p.rssi, p.name or self.names.get(p.mac, "")))
        self.peers = out

    def learn(self, peer: Peer | None) -> bool:
        """A streamed name. True when it changed what should be shown."""
        if not peer or not peer.name:
            return False
        self.names_resolved = True
        if self.names.get(peer.mac) == peer.name:
            return False
        self.names[peer.mac] = peer.name
        self.merge(self.peers)
        return True

    def rows(self, sending_to: str = "", sending_name: str = "") -> list[Peer]:
        """The devices to list. The one a send is going to stays until that
        send is over, even if a poll stops hearing it mid-transfer."""
        rows = list(self.peers)
        if sending_to and all(p.mac != sending_to for p in rows):
            rows.append(Peer(sending_to, None, self.names.get(sending_to, sending_name)))
        return rows

    def clear(self) -> None:
        self.peers = []
        self.names_resolved = False


def radar_status(st: Status, scanning: bool, book: PeerBook) -> str:
    if not st.receiving:
        return "Turning Fdrop on…"
    if not scanning:
        return "Waiting for the radio…"
    if not book.peers:
        return "Searching for devices…"
    if not book.names_resolved:
        return "Resolving device names…"
    return ""


def signal_strength(rssi: int | None) -> float:
    """0..1 as the radar places it, from -90 dBm (edge) to -30 dBm (centre)."""
    if rssi is None:
        return 0.0
    return max(0.0, min(1.0, (rssi + 90) / 60))


def bearing(mac: str) -> int:
    """A stable angle per address: AWDL gives no direction, so each device
    gets a fixed place rather than one that jumps on every poll."""
    h = 0
    for ch in mac:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return h % 360


# ----------------------------------------------------------------- sending

_SENDING = re.compile(r"^sending (.*) to \S+ at ")
_WAITING = re.compile(r"^waiting up to \d+s for .* to start listening")
_PREPARING = re.compile(r"^Preparing (.*)\.\.\.$")


def preparing_what(line: str) -> str | None:
    m = _PREPARING.match(line.strip())
    return m.group(1) if m else None


def send_status_line(line: str, what: str, who: str) -> str:
    """One line of `omdrop send`, as the row shows it. The sender speaks of
    ports and paths; from here both are simply the file on its way."""
    line = line.strip()
    m = _SENDING.match(line)
    if m or _WAITING.match(line):
        thing = what or (os.path.basename(m.group(1)) if m else "the file")
        return f"Sending {thing} to {who}…"
    return re.sub(r"^omdrop: ", "", line)


def describe_files(paths: list[str]) -> str:
    if len(paths) == 1:
        return os.path.basename(paths[0])
    return f"{len(paths)} files"


def is_web_url(text: str) -> bool:
    return bool(re.match(r"^https?://[^\s/]+\S*$", text.strip(), re.I))


# --------------------------------------------------------------- receiving

@dataclass
class Arrival:
    kind: str          # "ask" | "files" | "link"
    files: list = field(default_factory=list)
    text: str = ""


_ASK = re.compile(r"ask from (.*?) \((.*?)\): type \S+, (\d+) item\(s\)")


def parse_receiver_line(line: str, download_dir: str) -> Arrival | None:
    """One line of the receiver's journal. The same two lines `omdrop watch`
    announces from (cmd_watch), so the window lists exactly what was notified,
    plus the Ask that precedes an upload."""
    if ": stored " in line and "upload " in line:
        rest = line.split(": stored ", 1)[1]
        files = [_abs(f.strip(), download_dir) for f in rest.split(",") if f.strip()]
        return Arrival("files", files) if files else None
    if "link stored as " in line:
        stored = line.split("link stored as ", 1)[1].split(" -> ", 1)[0].strip()
        return Arrival("link", [_abs(stored, download_dir)]) if stored else None
    m = _ASK.search(line)
    if m:
        n = int(m.group(3))
        return Arrival("ask", text=f"{m.group(1)} is sending {n} item{'' if n == 1 else 's'}…")
    return None


def _abs(name: str, download_dir: str) -> str:
    return name if name.startswith("/") else os.path.join(download_dir, name)
