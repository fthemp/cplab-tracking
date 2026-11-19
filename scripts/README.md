# Scripts

## outage.sh: fault injection

```bash
scripts/outage.sh SECONDS [stop|freeze|drop] [remote|all]
```

| Mode | What happens | Device sees | Use it for |
|---|---|---|---|
| `stop` | Mosquitto service stopped, then restarted | connection closed at once | 10 s test, website relay test (port 9001 goes down too) |
| `freeze` | Mosquitto process paused, then resumed | nothing: silent outage | 14 s / 16 s test (exact duration, no restart delay) |
| `drop` | Port 1883 blocked by the firewall (`pf` on macOS, `iptables` on Linux) | nothing: silent outage | Wi-Fi-like loss of the Tab5 only, Node-RED keeps running |

Works on macOS (Mosquitto from Homebrew) and Linux (Mosquitto as a systemd service).
On Linux, `stop` and `freeze` also need root: see [docs/linux-setup.md](../docs/linux-setup.md).

Each run appends a line to `tests/outages.csv`: start and end (UTC, ms), requested and measured
duration, mode, result (`ok`, `cut_failed`, `restore_failed`, `interrupted`). The broker is
restored if the script is interrupted (Ctrl-C, or Node-RED killing it).

`stop` adds 0.5–2 s of restart time on top of SECONDS (measured in the log): don't use it for the 14 s test.

### Running it from Node-RED

Use an **exec** node, command `/absolute/path/to/scripts/outage.sh`, append `msg.payload`
(e.g. `16 freeze`). The script's output is the CSV line; the exit code is 0 when it worked.

### drop mode on macOS: one-time setup

`pfctl` needs root, and Node-RED cannot type a password. Allow only `pfctl` without a password,
with your own user name, then check it with `sudo -n pfctl -s info`:

```bash
echo "$(whoami) ALL=(root) NOPASSWD: /sbin/pfctl" | sudo tee /etc/sudoers.d/cplab-pfctl && sudo chmod 440 /etc/sudoers.d/cplab-pfctl
```

`remote` (default) drops only traffic from other machines: the simulator on the same laptop is not
cut. Use `all` to cut loopback too (Node-RED on the laptop is then cut as well).
Note: `drop` flushes the firewall state of every connection on the Mac, not only port 1883.
Remove the rule after the project: `sudo rm /etc/sudoers.d/cplab-pfctl`.
