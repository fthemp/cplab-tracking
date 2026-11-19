#!/bin/bash
# Fault injection for the MQTT outage tests, on the computer that runs Mosquitto.
# Works on macOS (Mosquitto from Homebrew) and Linux (Mosquitto as a systemd service).
#
# usage: scripts/outage.sh SECONDS [stop|freeze|drop] [remote|all]
#
#   stop   Stop the Mosquitto service. Clients see the TCP connection close at once.
#          Also cuts Node-RED and the website (port 9001): use it for the relay test.
#          Restarting takes 0.5-2 s on top of SECONDS (measured in the log).
#   freeze Pause the Mosquitto process (SIGSTOP), resume it (SIGCONT). Connections stay
#          open but nothing answers: a silent outage like drop, and the restore is
#          instant, so the duration is exact (use it for the 14 s / 16 s test).
#          Cuts every client, Node-RED and the website included.
#   drop   Keep Mosquitto running, silently drop packets to port 1883 with the firewall
#          (pf on macOS, iptables on Linux). Clients only notice through timeouts.
#          remote (default): only traffic from other machines (Tab5) is dropped;
#                            Node-RED and the simulator on this computer keep working.
#          all:              loopback too, to test the simulator on this computer.
#
# Root rights (sudo, never a password prompt: see scripts/README.md for the one-time rule):
#   macOS: drop only.   Linux: stop, freeze (Mosquitto runs as user "mosquitto") and drop.
#
# The SECONDS count starts when the broker is really unreachable. The log line has
# the measured outage (broker unreachable -> reachable again), in milliseconds.
# Log: tests/outages.csv (next to this script's folder, whatever the current dir).
# Exit code: 0 ok, 1 usage, 2 could not cut, 3 could not restore (broker still down!).
# Env: MOSQUITTO_SERVICE (Linux systemd unit, default "mosquitto").

# Node-RED's exec has a minimal PATH
export PATH=/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/local/sbin:/usr/bin:/bin:/usr/sbin:/sbin

S=${1:-}; MODE=${2:-stop}; SCOPE=${3:-remote}
PORT=1883
OS=${CPLAB_OS:-$(uname -s)}            # Darwin or Linux (CPLAB_OS only for testing)
SERVICE=${MOSQUITTO_SERVICE:-mosquitto}
DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$DIR/tests/outages.csv"

usage() { echo "usage: $0 SECONDS [stop|freeze|drop] [remote|all]" >&2; exit 1; }
[[ "$S" =~ ^[0-9]+(\.[0-9]+)?$ ]] || usage
[[ "$MODE" == stop || "$MODE" == freeze || "$MODE" == drop ]] || usage
[[ "$SCOPE" == remote || "$SCOPE" == all ]] || usage
[[ "$OS" == Darwin || "$OS" == Linux ]] || { echo "Unsupported system: $OS (macOS or Linux only)" >&2; exit 1; }
if [[ "$OS" == Darwin && "$MODE" == stop && $EUID -eq 0 ]]; then
  echo "Do not run stop mode with sudo on macOS: brew would restart Mosquitto as root." >&2; exit 1
fi

# ---------------------------------------------------------------- helpers --
now()  { perl -MTime::HiRes=time -e 'printf "%.3f", time'; }
iso()  { perl -MPOSIX=strftime -e '$t=shift; printf "%s.%03dZ", strftime("%Y-%m-%dT%H:%M:%S", gmtime($t)), ($t-int($t))*1000' "$1"; }
asroot() { if [[ $EUID -eq 0 ]]; then "$@"; else sudo -n "$@"; fi; }   # never prompts
port_open() {      # does something accept a TCP connection on the broker port?
  if [[ "$OS" == Darwin ]]; then nc -z -G 1 127.0.0.1 "$PORT" >/dev/null 2>&1
  else timeout 1 bash -c "exec 3<>/dev/tcp/127.0.0.1/$PORT" >/dev/null 2>&1; fi
}
broker_state() {   # process state letters of mosquitto: T = stopped (frozen)
  local p; p=$(pgrep -x mosquitto | head -1); [[ -n "$p" ]] && ps -o stat= -p "$p" | tr -d ' '
}
signal_broker() {  # SIGSTOP / SIGCONT; needs root if Mosquitto runs as another user
  pkill "-$1" -x mosquitto 2>/dev/null || asroot pkill "-$1" -x mosquitto
}
log() {   # start end requested mode result
  [[ -f "$LOG" ]] || echo "start_utc,end_utc,requested_s,actual_s,mode,result" > "$LOG"
  local actual=""; [[ -n "$2" ]] && actual=$(perl -e 'printf "%.3f", $ARGV[1]-$ARGV[0]' "$1" "$2")
  local line="$(iso "$1"),${2:+$(iso "$2")},$3,$actual,$4,$5"
  echo "$line" >> "$LOG"; echo "$line"
}

# --------------------------------------------------------- service control --
service_stop() {
  if [[ "$OS" == Darwin ]]; then brew services stop mosquitto >/dev/null 2>&1
  else asroot systemctl stop "$SERVICE" >/dev/null 2>&1; fi
}
service_start() {
  if [[ "$OS" == Darwin ]]; then brew services start mosquitto >/dev/null 2>&1
  else asroot systemctl start "$SERVICE" >/dev/null 2>&1; fi
}

# ---------------------------------------------------------------- firewall --
PF_TOKEN=""
IPT_RULE=(-p tcp --dport "$PORT" -j DROP)
[[ "$SCOPE" == remote ]] && IPT_RULE=(! -i lo "${IPT_RULE[@]}")
firewall_block() {
  if [[ "$OS" == Linux ]]; then
    # first rule of INPUT: applies to new and already open connections alike
    asroot iptables -I INPUT 1 "${IPT_RULE[@]}" >/dev/null 2>&1
    return
  fi
  local on="on ! lo0"; [[ "$SCOPE" == all ]] && on=""
  echo "block drop in quick $on proto tcp from any to any port $PORT" \
    | sudo -n pfctl -q -a com.apple/cplab -f - 2>/dev/null || return 1
  PF_TOKEN=$(sudo -n pfctl -E 2>&1 | awk '/Token/{print $3}')
  [[ -n "$PF_TOKEN" ]] || return 1
  # Existing connections bypass pf rules through their state: flush them.
  # Note: this flushes the state of every connection on the Mac, not only 1883.
  sudo -n pfctl -q -F states >/dev/null 2>&1
  return 0
}
firewall_unblock() {
  if [[ "$OS" == Linux ]]; then
    asroot iptables -D INPUT "${IPT_RULE[@]}" >/dev/null 2>&1
    return
  fi
  sudo -n pfctl -q -a com.apple/cplab -F rules >/dev/null 2>&1 || return 1
  [[ -n "$PF_TOKEN" ]] && sudo -n pfctl -q -X "$PF_TOKEN" >/dev/null 2>&1
  return 0
}

# ------------------------------------------------------------ cut / restore --
cut() {
  case "$MODE" in
    stop)
      service_stop || return 1
      for _ in $(seq 50); do port_open || return 0; sleep 0.1; done   # wait until really closed
      return 1 ;;
    freeze)
      signal_broker STOP || return 1
      [[ "$(broker_state)" == T* ]] ;;                                # check it is really paused
    drop)
      firewall_block ;;
  esac
}
restore() {
  case "$MODE" in
    stop)
      service_start
      for _ in $(seq 150); do port_open && return 0; sleep 0.1; done  # wait until listening
      return 1 ;;
    freeze)
      signal_broker CONT || return 1
      [[ "$(broker_state)" != T* ]] ;;
    drop)
      firewall_unblock ;;
  esac
}

T0=""
on_interrupt() {   # Ctrl-C or Node-RED kill: never leave the broker down
  restore; log "${T0:-$(now)}" "$(now)" "$S" "$MODE" interrupted; exit 3
}
trap on_interrupt INT TERM HUP

if [[ "$MODE" != drop ]] && ! port_open; then
  echo "Mosquitto is not listening on $PORT before the test." >&2; exit 2
fi
if ! cut; then
  restore; log "$(now)" "" "$S" "$MODE" "cut_failed"
  echo "Could not cut the broker in $MODE mode. If it needs root, is the sudo rule installed? (scripts/README.md)" >&2
  exit 2
fi
T0=$(now)
REMAIN=$(perl -e 'my $r=$ARGV[0]+$ARGV[1]-$ARGV[2]; printf "%.3f", $r>0?$r:0' "$T0" "$S" "$(now)")
sleep "$REMAIN" & wait $!          # 'wait' lets the trap fire immediately on Ctrl-C
trap - INT TERM HUP
if restore; then log "$T0" "$(now)" "$S" "$MODE" ok; exit 0; fi
log "$T0" "" "$S" "$MODE" restore_failed; exit 3
