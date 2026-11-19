# Running the outage tests on Linux

Step-by-step setup to run the broker, Node-RED, the simulated Tab5s and the outage tests on a
Linux computer (written for Ubuntu 24.04 or newer; other Debian-based systems are similar).
Everything below runs on that one computer. Real Tab5s only need its IP address in their `config.h`.

## 1. Mosquitto

```bash
sudo apt install mosquitto mosquitto-clients
```

Copy the project's configuration as an extra file (Ubuntu's main config reads `conf.d/`):

```bash
sudo cp mosquitto/mosquitto.conf /etc/mosquitto/conf.d/cplab.conf
```
```bash
sudo systemctl restart mosquitto && systemctl status mosquitto --no-pager
```

If it does not start, `journalctl -u mosquitto -n 30` shows why. Ubuntu's main config
already sets `persistence` and `persistence_location /var/lib/mosquitto/`: that's expected.
Open ports 1883 (MQTT) and 9001 (WebSockets) if a firewall (`ufw`) is active:

```bash
sudo ufw allow 1883/tcp && sudo ufw allow 9001/tcp && sudo ufw allow 1880/tcp && sudo ufw allow 4840/tcp
```

## 2. Node-RED

Node.js 20 or newer, then Node-RED ([official guide](https://nodered.org/docs/getting-started/local)).
With Node.js from `apt` (`sudo apt install nodejs npm`):

```bash
sudo npm install -g --unsafe-perm node-red
```

With Node.js from **nvm**, install without `sudo` (nvm lives in your home folder):

```bash
npm install -g node-red
```

Node-RED warns that nvm is not recommended. Two reasons, and neither blocks these tests:
Node-RED is installed inside one Node.js version (if you change nvm's default, the `node-red`
command disappears until you switch back), and a background service started at boot does not
load nvm. Keep nvm's default on the version you installed Node-RED with
(`nvm alias default 24`), and start Node-RED from a terminal. Don't install `apt`'s Node.js
as well: two Node.js on one machine is how the wrong one gets used.

Start it once (`node-red`), stop it with Ctrl-C, then:

1. In `~/.node-red/settings.js`, set `mqttReconnectTime: 2000,` (the line exists, with 15000).
2. Install the two palette modules:
   ```bash
   cd ~/.node-red && npm install @flowfuse/node-red-dashboard node-red-contrib-opcua
   ```
3. Start Node-RED with the path of this repository, so the test buttons find `outage.sh`:
   ```bash
   CPLAB_REPO="$HOME/cplab-tracking" node-red
   ```
   (adapt the path to where you cloned the repository). From an SSH command that does not load
   your shell's settings (so no nvm), add nvm's Node.js to the PATH first, e.g.
   `export PATH=~/.nvm/versions/node/v24.22.3/bin:$PATH`, otherwise: `env: 'node': No such file`.
4. Open `http://localhost:1880`, menu > Import > select `nodered/cplab_flows.json` > Deploy.
   Dashboard: `http://localhost:1880/dashboard`, page **Fiabilite** for the tests.

## 3. Root rights for the outage script

On Linux, Mosquitto runs as its own system user (`mosquitto`), so stopping, freezing or
firewalling it needs root. Node-RED cannot type a password, so allow **only these exact
commands** without one. Replace `YOUR_USER` by your user name (`whoami`):

```bash
sudo visudo -f /etc/sudoers.d/cplab-outage
```

and paste this single line (it's long; keep it on one line):

```
YOUR_USER ALL=(root) NOPASSWD: /usr/bin/systemctl stop mosquitto, /usr/bin/systemctl start mosquitto, /usr/bin/pkill -STOP -x mosquitto, /usr/bin/pkill -CONT -x mosquitto, /usr/sbin/iptables -I INPUT 1 ! -i lo -p tcp --dport 1883 -j DROP, /usr/sbin/iptables -D INPUT ! -i lo -p tcp --dport 1883 -j DROP, /usr/sbin/iptables -I INPUT 1 -p tcp --dport 1883 -j DROP, /usr/sbin/iptables -D INPUT -p tcp --dport 1883 -j DROP
```

`visudo` checks the syntax before saving (a broken sudoers file can lock you out of `sudo`,
which is why we don't edit it with a normal editor). Check it works, it must not ask for a password:

```bash
sudo -n systemctl stop mosquitto && sudo -n systemctl start mosquitto && echo OK
```

Remove the rule after the project: `sudo rm /etc/sudoers.d/cplab-outage`.

## 4. Simulated Tab5s

```bash
sudo apt install python3-venv
```
```bash
cd simulator && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```
```bash
.venv/bin/python -m pytest -q tests
```
```bash
.venv/bin/python device_sim.py --dev prod-st1 --dev prod-st2 --dev prod-st3 --dev prod-color --dev prod-hmi --dev maint-m1 --dev maint-m2 --opc opc.tcp://localhost:4840/ --cycle 1
```

## 5. Running the tests

Either the buttons on the dashboard page **Fiabilite** (results in `tests/test_matrix.csv`), or
by hand from the repository folder:

```bash
scripts/outage.sh 16 freeze
```

| Mode | Linux command used | Use for |
|---|---|---|
| `stop` | `systemctl stop/start mosquitto` | 10 s test, website relay test |
| `freeze` | `pkill -STOP/-CONT -x mosquitto` | 14 s / 16 s test (exact duration) |
| `drop` | `iptables` rule on port 1883 | cutting only the real Tab5s (`remote`), or everything (`all`) |

If Mosquitto's systemd unit has another name (e.g. a snap install: `snap.mosquitto.mosquitto`),
set it with `MOSQUITTO_SERVICE=snap.mosquitto.mosquitto scripts/outage.sh 10 stop`, and adapt the
sudo rule to that name.

## Differences with macOS

| | macOS | Linux |
|---|---|---|
| Mosquitto config | `/opt/homebrew/etc/mosquitto/mosquitto.conf` | `/etc/mosquitto/conf.d/cplab.conf` |
| Service | `brew services` | `systemctl` |
| Firewall for `drop` | `pf` (`pfctl`) | `iptables` |
| Root needed for | `drop` only | `stop`, `freeze`, `drop` |
| Status | tested | tested on Ubuntu 26.04 (Nov 20), see below |

## Results on Linux (Nov 20)

Ubuntu 26.04, Mosquitto 2.0.22 (`apt`), Node.js 24 (nvm), Node-RED 5.0.10, 3 simulated Tab5s
(`prod-st1`, `prod-color`, `maint-m1`, `--cycle 0.5`), all on the same PC, tests started from
the dashboard through an SSH tunnel (`ssh -L 18800:localhost:1880 your-computer-name`).

| Test | Broker really down | Missing | Back on MQTT | Verdict |
|---|---|---|---|---|
| 10 s `stop` | 10.04 s | 0 | 7.1–9.1 s | PASS 3/3 |
| 14 s `freeze` | 14.09 s | 0 | 5.4–8.1 s, no OPC UA | PASS 3/3 |
| 16 s `freeze` | 16.09 s | 0 | 5.0 s, OPC UA at 14.2–14.9 s | PASS 3/3 |
| 3 × 5 s `stop` | 3 × 5.04 s | 0 | 6.0–11.6 s | First run FAIL 2/3 (0.4 s and 0.9 s over the return limit); after the fix PASS 3/3 |
| Outage during replay | 14.09 + 5.09 s | 0 | 6.0–10.3 s | PASS 3/3 |
| Overflow, `prod-color --buffer-max 20` | 12.05 s | 2, then 0 | 7.1–8.8 s | First run FAIL (2 of the 21 dropped already had a `seq`); after the fix PASS: 25 dropped, 0 missing |
| 6 s `drop all` (by hand) | 6.02 s | not counted | 5.5–6.5 s after the rule was removed | firewall rule added and removed, Tab5s detected the silent outage |

What differs from macOS: `systemctl` stops and restarts Mosquitto almost instantly (10.04 s
for 10 s, against 10.7 s with `brew services`). That showed two gaps in the model, both fixed
on Nov 20 (spec v5 sections 5 and 10) and retested:

- **Return limit.** Reconnecting took about 1 s more than the 2 s retry interval (connection,
  subscription, timer steps), so with a long backlog the Tab5s landed just over the limit. The
  limit now allows 2 s of reconnection overhead.
- **Overflow.** The messages sent just before the stop are put back at the front of the buffer
  with their `seq` (their arrival was not confirmed). The overflow dropped the oldest first:
  those, which showed as a gap in Node-RED. It now drops the oldest message without a `seq`.
  On macOS the slower stop left nothing in flight.
