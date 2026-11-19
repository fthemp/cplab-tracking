# Simulated devices for the outage tests

Python stand-ins for the Tab5, with the outage handling of the spec
([docs/spec-mqtt-outage-handling-v5.md](../docs/spec-mqtt-outage-handling-v5.md)), so the
Node-RED flows, the website and the outage tests can be built and checked without hardware.

| File | What it does |
|---|---|
| `reliability.py` | The 4 states, buffer, replay, `seq`, status topic. No network code: a readable model for the firmware. |
| `device_sim.py` | Simulated `prod-st1..3`, `prod-color`, `prod-hmi`, `maint-m1..4`: same topics and payloads as the firmware, plus `seq` and `cplab/rel/<devId>/status`. |
| `seq_monitor.py` | Loss check by `seq` on MQTT + OPC UA: sent vs received, missing, duplicates. What the Node-RED loss check must do. |
| `opcua_test_server.py` | Stand-in for the Node-RED OPC UA server (port 4840), to test without Node-RED. |
| `tests/test_reliability.py` | The acceptance scenarios of spec section 9 on a fake clock (runs in under a second). |

## Setup

Python 3.11 or newer, Mosquitto running on the laptop.

```bash
cd simulator
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q tests
```

## With Node-RED

Start the simulated devices against Node-RED's OPC UA server, then use the buttons on the
dashboard page **Fiabilite** (see [nodered/README.md](../nodered/README.md)):

```bash
.venv/bin/python device_sim.py --dev prod-st1 --dev prod-color --dev prod-hmi --dev maint-m1 --dev maint-m2 --opc opc.tcp://localhost:4840/ --cycle 0.5
```

Results on 2026-11-19 (5 simulated devices, Node-RED 5.0, `node-red-contrib-opcua` 0.2.355):

| Test | Missing | Switch to OPC UA | Back on MQTT | Verdict |
|---|---|---|---|---|
| 16 s `freeze` | 0 | 15.7–15.8 s | 5.0–5.1 s | PASS (5/5) |
| 14 s `freeze` | 0 | none | 5.2–7.9 s | PASS (5/5) |
| 10 s `stop` | 0 | none | 6.4–8.4 s | PASS (5/5) |

## Without Node-RED

Four terminals, from `simulator/`:

```bash
.venv/bin/python opcua_test_server.py --dev prod-st1 --dev prod-color --dev maint-m1
```
```bash
.venv/bin/python seq_monitor.py --opc opc.tcp://localhost:4840 --dev prod-st1 --dev prod-color --dev maint-m1 --csv ../tests/seq_results.csv --label "16 s freeze"
```
```bash
.venv/bin/python device_sim.py --dev prod-st1 --dev prod-color --dev maint-m1 --opc opc.tcp://localhost:4840 --cycle 0.5
```
```bash
../scripts/outage.sh 16 freeze
```

Wait until the simulator prints `CONNECTED` again, then stop the monitor with Ctrl-C: it prints
`PASS` / `FAIL` per device and appends the result to `tests/seq_results.csv`.
Without `--opc`, a device in `OPC_FALLBACK` keeps its messages and replays them over MQTT on return.

## Results on 2026-11-19 (MacBook, local Mosquitto, 3 simulated devices, `--cycle 0.5`)

| Test | Broker really down | Result |
|---|---|---|
| 10 s `stop` | 10.7 s | 0 missing, no OPC UA, back on MQTT 8 s after restart |
| 14 s `freeze` (silent) | 14.05 s | 0 missing, no OPC UA, back on MQTT 7 s after resume |
| 16 s `stop` | 17.9 s | 0 missing, switch to OPC UA at 15.0 s, back on MQTT 7 s after restart |
| 16 s `freeze` (silent) | 16.04 s | 0 missing, switch to OPC UA 14 s after the freeze |

Main loop worst gap: 26–78 ms (limit 100 ms). Unit tests also cover 3 × 5 s outages,
an outage during replay, buffer overflow and the replay rate.

## Choices made in the simulator (now in spec v5, some still to confirm)

1. **Detecting a silent outage.** When packets are dropped (or the broker hangs), publishing does
   not fail: TCP accepts the data. With `keepalive 15` the Tab5 would notice after 15–30 s, too late
   for the 15 s switch. So each device publishes its status topic every second and subscribes to it;
   no echo for 3 s means the link is lost, and the outage started at the last echo.
2. **Messages sent since the last echo** may be lost: they are kept and replayed. Node-RED drops the
   copies by `seq` (the monitor shows them as `dup_dropped`).
3. **`seq` is given when a message is first sent**, not when it is created. A buffered `count`
   replaced by a newer one never gets a number, so it does not look like a loss.
4. **Heartbeat, status and online/offline carry no `seq`** (they are never buffered).
5. **The 15 s timer pauses** once the broker answers again, during the `T_STABLE` wait.
6. **OPC UA nodes**: one String variable per topic, `ns=1;s=<full topic>`, value = the JSON payload.
   The Node-RED OPC UA server must create these nodes (or tell us its naming, `--opc-ns` changes the namespace).

## Status topic payload

`cplab/rel/<devId>/status` (retained, every second and on each change):

```json
{"id":"prod-st1","boot":"3f9c1a2b","state":"BUFFERING","path":"BUFFER","buffer":12,"capacity":500,
 "dropped":0,"seq":42,"sent":42,"replayed":0,"outage_s":6.2,"loop_max_ms":21.0,"probe":118,"ts":1763578931000}
```

`path` is `MQTT`, `BUFFER`, `OPCUA` or `REPLAY`. `boot` changes at each restart, when `seq` starts again at 1.
