#!/bin/bash
# Find out why AirDrop does not get through, in both directions, and leave a
# report directory to read afterwards.
#
#   tools/airdrop-diagnose.sh [SECONDS-TO-WAIT-FOR-THE-PHONE]     (default 180)
#   tools/airdrop-diagnose.sh --watch
#
# --watch turns nothing on or off. It waits for you to switch on in the app,
# records until you switch off again, and then summarises: for a transfer that
# works from here and not from the app.
#
# Run it as yourself, in a terminal, with the iPhone beside the laptop. It asks
# for your sudo password once, to capture packets on awdl0 and to read the
# firewall's rules; it changes no firewall rule and no setting.
#
# Wi-Fi is OFF while it runs (the Intel card cannot do both) and comes back
# when it ends, also on Ctrl-C.
set -uo pipefail

WATCH=0
[ "${1:-}" = --watch ] && { WATCH=1; shift; }
WAIT=${1:-180}
[ "$(id -u)" != 0 ] || { echo "Run this as yourself, not with sudo."; exit 2; }
command -v omdrop >/dev/null || { echo "The omdrop command is not installed."; exit 2; }

OUT=$HOME/omdrop-diagnose-$(date +%Y%m%d-%H%M%S)
mkdir -p "$OUT"
exec > >(tee -a "$OUT/console.log") 2>&1
RUN=/run/omdrop-owl
PORT=8771
step() { printf '\n== %s\n' "$*"; }

if (( WATCH )); then echo "Recording the next time Omdrop is on. Report: $OUT"
else echo "This turns Wi-Fi off for a few minutes. Report: $OUT"
fi
sudo -v || { echo "sudo is needed for the packet capture."; exit 2; }
# Keep the sudo timestamp alive: the capture is stopped with it at the end.
( while sleep 50; do sudo -n true 2>/dev/null || exit; done ) &
KEEP=$!

CAP=
cleanup() {
  trap - EXIT INT TERM
  (( WATCH )) && step "Collecting" || step "Turning Omdrop off and giving Wi-Fi back"
  [ -n "$CAP" ] && sudo -n kill "$CAP" 2>/dev/null
  [ -n "${JR:-}" ] && kill "$JR" 2>/dev/null
  sleep 1
  # The state the window ended in, before it is torn down.
  { grep -v TRACE "$RUN/owl.log" 2>/dev/null; } > "$OUT/owl.log"
  cp "$RUN/log" "$OUT/helper.log" 2>/dev/null
  tail -n 20 "$RUN/announce.log" > "$OUT/announce.log" 2>/dev/null
  (( WATCH )) || omdrop off --force
  kill "$KEEP" 2>/dev/null
  sudo -n chown "$(id -u):$(id -g)" "$OUT"/*.pcap 2>/dev/null
  summarise
  echo
  echo "Done. The report is in $OUT"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

count() { tcpdump -nn -r "$OUT/awdl0.pcap" "$@" 2>/dev/null | wc -l; }

summarise() {
  step "Summary"
  local pcap=$OUT/awdl0.pcap me
  [ -s "$pcap" ] || { echo "no packet capture was written"; return; }
  me=$(cat "$OUT/our-address" 2>/dev/null)
  {
    echo "our address on awdl0:                 ${me:-unknown}"
    echo "AWDL peers OWL synced with:           $(grep -c 'add peer' "$OUT/owl.log" 2>/dev/null)"
    echo "packets captured:                     $(count)"
    echo "packets from other devices:           $(count "ip6 and not src host $me")"
    echo "mDNS packets from other devices:      $(count "udp port 5353 and not src host $me")"
    echo "mDNS packets we sent:                 $(count "udp port 5353 and src host $me")"
    echo "neighbour solicitations for us:       $(count "icmp6 and ip6[40] == 135 and not src host $me")"
    echo "neighbour advertisements we sent:     $(count "icmp6 and ip6[40] == 136 and src host $me")"
    echo "TCP SYNs to our receiver ($PORT):       $(count "ip6 and tcp dst port $PORT and ip6[53] & 0x12 == 0x02")"
    echo "SYN-ACKs our receiver answered:       $(count "ip6 and tcp src port $PORT and ip6[53] & 0x12 == 0x12")"
    echo "ICMPv6 'prohibited' we sent (firewall): $(count "icmp6 and ip6[40] == 1 and src host $me")"
    echo "receiver: connections logged:         $(grep -c 'connection from' "$OUT/receiver.log" 2>/dev/null)"
    echo "receiver: files stored:               $(grep -c ': stored \|link stored as' "$OUT/receiver.log" 2>/dev/null)"
    echo "send to the phone:                    $(cat "$OUT/send-result" 2>/dev/null || echo 'not tried')"
    echo "times OWL lost the phone:             $(grep -c 'remove peer' "$OUT/owl.log" 2>/dev/null)"
    echo "TCP SYNs we sent to port 8770:        $(count "ip6 and tcp dst port 8770 and ip6[53] & 0x12 == 0x02")"
    [ -s "$OUT/helper-calls.txt" ] && echo "Scan polls (helper 'peers' calls):    $(grep -c ' omdrop-discoverable peers' "$OUT/helper-calls.txt")"
  } | tee "$OUT/summary.txt"
  # What the phone asked for over mDNS, by name.
  tcpdump -nn -v -r "$pcap" "udp port 5353 and not src host $me" 2>/dev/null \
    | grep -o -E '(PTR|SRV|TXT|AAAA|ANY)\? [^ ,]+' | sort | uniq -c | sort -rn | head -n 15 > "$OUT/mdns-questions.txt"
  [ -s "$OUT/mdns-questions.txt" ] && { echo "what other devices asked over mDNS:"; cat "$OUT/mdns-questions.txt"; }
}

step "System"
{
  date; uname -r
  rpm -q omdrop fdrop omdrop-owl firewalld avahi NetworkManager
  echo "omdrop: $(command -v omdrop) -> $(readlink -f "$(command -v omdrop)")"
  omdrop version
  echo "--- doctor"; omdrop doctor
  echo "--- firewalld"; systemctl is-active firewalld; firewall-cmd --get-default-zone; firewall-cmd --list-all
  echo "--- firewalld.conf"; sudo -n grep -E '^(IPv6_rpfilter|LogDenied|FirewallBackend)' /etc/firewalld/firewalld.conf
  echo "--- services"; systemctl is-active avahi-daemon bluetooth NetworkManager
  echo "--- bluetooth"; bluetoothctl show 2>/dev/null | grep -E 'Powered|Discoverable|Name'
  echo "--- selinux"; getenforce 2>/dev/null
  echo "--- config"; cat "${XDG_CONFIG_HOME:-$HOME/.config}/airdrop/config.toml" 2>/dev/null
} > "$OUT/system.txt" 2>&1
echo "saved"

journalctl --user -u airdrop-receiver.service -f -n 0 -o cat > "$OUT/receiver.log" 2>/dev/null &
JR=$!
if (( WATCH )); then
  step "Waiting for you to switch on in the app (Ctrl-C to give up)"
  until ip link show awdl0 >/dev/null 2>&1; do sleep 0.2; done
  # The helper's calls show when Scan was running.
  SINCE=$(date '+%Y-%m-%d %H:%M:%S')
else
  step "Turning Omdrop on (Wi-Fi goes off now)"
  if ! omdrop on 15m; then
    echo "omdrop on failed; see above."
    exit 1
  fi
fi
for _ in $(seq 50); do ip -6 -o addr show dev awdl0 scope link 2>/dev/null | grep -q inet6 && break; sleep 0.1; done
ip -6 -o addr show dev awdl0 scope link | awk '{sub(/\/.*/, "", $4); print $4; exit}' > "$OUT/our-address"
sudo -n tcpdump -i awdl0 -U -Z root -w "$OUT/awdl0.pcap" >/dev/null 2>&1 &
sleep 0.5
CAP=$(pgrep -n -x tcpdump)
{
  echo "--- addresses"; ip -6 addr show dev awdl0; ip -br link
  echo "--- routes"; ip -6 route show dev awdl0
  echo "--- listening"; ss -Hltnp "sport = :$PORT"
  echo "--- zone of awdl0"; firewall-cmd --get-zone-of-interface=awdl0 2>&1
  echo "--- nft ruleset"; sudo -n nft list ruleset
} > "$OUT/network.txt" 2>&1
omdrop status

if (( WATCH )); then
  step "Recording. Use the app and the phone now; switch off in the app when you are done."
  t=0
  while ip link show awdl0 >/dev/null 2>&1; do
    (( t % 10 )) || printf '  %3d s   peers now: %s   connections: %s   stored: %s\n' "$t" \
      "$(( $(grep -c 'add peer' "$RUN/owl.log" 2>/dev/null) - $(grep -c 'remove peer' "$RUN/owl.log" 2>/dev/null) ))" \
      "$(grep -c 'connection from' "$OUT/receiver.log")" \
      "$(grep -c ': stored \|link stored as' "$OUT/receiver.log")"
    sleep 1; t=$(( t + 1 ))
  done
  journalctl --since "$SINCE" --no-pager -o short-iso 2>/dev/null \
    | sed -n 's/^\([^ ]*\) .*COMMAND=\/usr\/lib\/omdrop\/\(.*\)\]$/\1 \2/p' > "$OUT/helper-calls.txt"
  ip -6 neigh show dev awdl0 > "$OUT/neighbours.txt" 2>&1
  exit 0
fi

step "Phone -> computer (up to $WAIT s)"
cat <<EOF
On the iPhone, now:
  1. Control Center > AirDrop > "Everyone for 10 Minutes".
  2. Photos > open a photo > Share > AirDrop.
  3. Keep that sheet open and the screen on. If "$(omdrop name)" appears, tap it.
EOF
received=no
for (( t = 0; t < WAIT; t += 5 )); do
  printf '  %3d s   peers synced: %s   connections: %s   stored: %s\n' "$t" \
    "$(grep -c 'add peer' "$RUN/owl.log" 2>/dev/null)" \
    "$(grep -c 'connection from' "$OUT/receiver.log")" \
    "$(grep -c ': stored \|link stored as' "$OUT/receiver.log")"
  if grep -q ': stored \|link stored as' "$OUT/receiver.log"; then received=yes; break; fi
  sleep 5
done
echo "phone -> computer: $received"
ip -6 neigh show dev awdl0 > "$OUT/neighbours.txt" 2>&1

step "Computer -> phone"
echo "Keep the AirDrop sheet open on the iPhone. Asking nearby devices for their names..."
timeout 75 omdrop peers -n 2>&1 | tee "$OUT/peers.txt"
mac=$(grep -o -E '^([0-9a-f]{2}:){5}[0-9a-f]{2}' "$OUT/peers.txt" | head -n 1)
if [ -z "$mac" ]; then
  echo "no device answered" | tee "$OUT/send-result"
else
  file=$OUT/omdrop-test.txt
  echo "Omdrop test from $(hostname -s) at $(date)" > "$file"
  echo "Sending a small text file to $mac. Accept it on the iPhone when asked."
  if timeout 120 omdrop send -v --to "$mac" "$file" 2>&1 | tee "$OUT/send.log"; then
    echo "ok" > "$OUT/send-result"
  else
    echo "failed (see send.log)" > "$OUT/send-result"
  fi
  cat "$OUT/send-result"
fi
