#!/usr/bin/python3
"""A stand-in for the omdrop command, for the front end's tests.

It answers the commands the window runs, in the real CLI's formats, from a
JSON file in $FAKE_OMDROP_STATE, and appends every invocation to `calls`
there. No radio, no systemd, nothing privileged.
"""
import json
import os
import signal
import sys
import time

STATE_DIR = os.environ["FAKE_OMDROP_STATE"]
STATE = os.path.join(STATE_DIR, "state.json")
DEFAULT = {"receiving": False, "visible": False, "remaining": None, "name": "fedora",
           "dir": os.path.join(STATE_DIR, "Downloads"), "mode": "off", "reason": "stopped",
           "rx_proven": False, "sound": True, "visibility": "everyone", "senders": 0,
           "sender_list": [], "usable": True, "refuse_on": "", "send": "ok",
           "peers": [{"mac": "0a:bc:de:f0:12:34", "rssi": -48, "name": None},
                     {"mac": "0a:11:22:33:44:55", "rssi": None, "name": None}],
           "names": {"0a:bc:de:f0:12:34": "Sam’s iPhone"}}


def load():
    try:
        with open(STATE) as f:
            return {**DEFAULT, **json.load(f)}
    except (OSError, ValueError):
        return dict(DEFAULT)


def save(s):
    with open(STATE + ".tmp", "w") as f:
        json.dump(s, f)
    os.replace(STATE + ".tmp", STATE)


def main(argv):
    with open(os.path.join(STATE_DIR, "calls"), "a") as f:
        f.write(json.dumps(argv) + "\n")
    s = load()
    cmd, args = (argv[0] if argv else ""), argv[1:]

    def turn_on(arg):
        if s["refuse_on"]:
            sys.stderr.write(s["refuse_on"] + "\n")
            return 1
        s.update(receiving=True, visible=True, reason="ok")
        if arg in ("once", "forever"):
            s.update(mode=arg, remaining=None)
        else:
            s.update(mode="timed", remaining=int(arg.rstrip("m")) * 60)
        save(s)
        return 0

    def turn_off():
        s.update(receiving=False, visible=False, remaining=None, mode="off", reason="stopped")
        save(s)
        return 0

    if cmd == "status":
        out = {k: s[k] for k in ("receiving", "remaining", "name", "dir", "mode", "reason",
                                 "rx_proven", "sound", "visibility")}
        out["visible"] = s["visible"] if s["usable"] else None
        out["senders"] = len(s["sender_list"])
        print(json.dumps(out))
    elif cmd == "toggle":
        return turn_off() if s["receiving"] else turn_on(args[0] if args else "10m")
    elif cmd == "on":
        return turn_on(args[0] if args else "10m")
    elif cmd == "off":
        return turn_off()
    elif cmd == "doctor":
        missing = [] if s["usable"] else [{"id": "radio", "say": "No radio backend is installed."}]
        if args[:1] == ["--json"]:
            print(json.dumps({"ready": not missing, "missing": missing}))
        else:
            print("Ready to receive." if not missing else "Not ready to receive yet:\n  - " + missing[0]["say"])
    elif cmd == "version":
        print("0.16.1-fake")
    elif cmd == "peers":
        if "--stream" in args:
            for p in s["peers"]:
                time.sleep(0.05)
                print(json.dumps({**p, "name": s["names"].get(p["mac"])}), flush=True)
        else:
            print(json.dumps(s["peers"]))
    elif cmd == "name":
        if not args:
            print(s["name"])
        elif len(args[0].encode()) > 63:
            sys.stderr.write("omdrop: that name is too long\n")
            return 1
        else:
            s["name"] = args[0]
            save(s)
    elif cmd == "dir":
        if args and not os.path.isdir(args[0]):
            sys.stderr.write(f"{args[0]}: not a directory (create it first)\n")
            return 1
        if args:
            s["dir"] = args[0]
            save(s)
        print(s["dir"])
    elif cmd == "sound":
        s["sound"] = args[0] == "on"
        save(s)
    elif cmd == "visibility":
        s["visibility"] = args[0]
        save(s)
    elif cmd == "senders":
        if args[0] == "list":
            print(json.dumps(s["sender_list"]))
        elif args[0] == "add":
            if "@" not in args[1] and not args[1].lstrip("+").isdigit():
                sys.stderr.write("omdrop: an email address or a phone number, please\n")
                return 1
            s["sender_list"].append(args[1])
            save(s)
        elif args[0] == "remove":
            s["sender_list"] = [i for i in s["sender_list"] if i != args[1]]
            save(s)
    elif cmd == "send":
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
        mac = args[args.index("--to") + 1]
        sys.stderr.write(f"waiting up to 30s for {mac} to start listening on 8770\n")
        sys.stderr.flush()
        if s["send"] == "hang":
            time.sleep(60)
        print("Asking them to accept it...", flush=True)
        time.sleep(0.1)
        if s["send"] == "declined":
            sys.stderr.write("They declined it.\n")
            return 1
        print("They accepted. Sending...", flush=True)
        time.sleep(0.1)
        print("Sent photo.jpg (2.4 MB in 10.5 s)", flush=True)
    else:
        sys.stderr.write(f"fake omdrop: no such command {cmd}\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
