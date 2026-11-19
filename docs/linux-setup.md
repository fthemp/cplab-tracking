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

Node.js 20 or newer, then Node-RED ([official guide](https://nodered.org/docs/getting-started/local)):

```bash
sudo npm install -g --unsafe-perm node-red
```

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
   (adapt the path to where you cloned the repository).
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
| Status | tested | logic tested on macOS with stand-in commands, not yet on a real Linux machine |
