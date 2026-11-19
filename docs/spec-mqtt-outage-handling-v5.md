# Spec: MQTT Outage Handling with Local Buffer and OPC UA Fallback

WirelessIoT-LAB2 (CPLab) · v0.5 · Nov 19, 2026 · Updated after the laptop-side implementation (student C). Changes since v0.4 are listed in section 13; items marked **to confirm** need the group's agreement.

## 1. Goal

When the MQTT broker becomes unreachable, a Tab5 must not lose production or maintenance data. Outage under 15 s: buffer locally and replay over MQTT. Outage of 15 s or more: switch to OPC UA. When the broker returns, go back to MQTT cleanly.

## 2. Scope

**In:** connection handling in `cplab_common.h`, local buffer, OPC UA path, connection status on the Tab5 screen and Node-RED dashboard, Mosquitto and Node-RED configuration. **Out:** Wi-Fi loss (the fallback assumes the network is up and only the broker is down), changes to Kanban or vibration logic, changes to existing topics, commands sent *to* the Tab5s during an outage (they still need MQTT).

## 3. Definitions

- **Echo:** each Tab5 publishes its status topic (section 8) every second **and subscribes to it**. Receiving its own status back proves the broker works right now.
- **Outage start (t0):** the time of the last echo received, or the moment the TCP connection closes, whichever comes first. A failed publish is *not* a reliable signal: with QoS 0 the network stack accepts the data even when the broker is frozen or unreachable, and the MQTT keepalive would only notice after 15 to 30 s (see section 4).
- **Outage duration:** `millis() - t0`.
- **Constants (configurable):**

| Constant | Value | Meaning |
| --- | --- | --- |
| `T_FALLBACK` | 15 s | Outage duration that triggers OPC UA |
| `T_STABLE` | 5 s | Broker reachable this long before replay starts |
| `T_PROBE` | 3 s | No echo for this long = connection lost |
| `STATUS_PERIOD` | 1 s | Status / echo period |
| `BUFFER_MAX` | 500 messages | Buffer + OPC UA queue (to confirm on the Tab5 PSRAM) |
| `REPLAY_RATE` | 20 msg/s | Replay speed |

## 4. States and transitions

| State | Entered when | Behaviour |
| --- | --- | --- |
| CONNECTED | Broker reachable and buffer empty | Publish directly over MQTT |
| BUFFERING | Connection lost (no echo for `T_PROBE`, or TCP closed), duration < 15 s. Also the state at boot, until the broker has been reachable for `T_STABLE` | Publishes go to the buffer; non-blocking reconnect attempt every 2 s |
| OPC\_FALLBACK | Duration reaches 15 s | Publishes go over OPC UA; MQTT reconnect attempt every 5 s continues in the background |
| RECOVERING | Broker reachable for `T_STABLE` | Replay buffer over MQTT, then return to CONNECTED |

Other transitions:

- BUFFERING to RECOVERING if the broker returns before 15 s.
- **The 15 s timer pauses** while the broker is reachable again and the Tab5 waits `T_STABLE`. If the broker drops again during that wait, or during RECOVERING, return to BUFFERING with the outage timer restarted. **(to confirm)**
- At the switch to OPC\_FALLBACK, buffered messages are written to OPC UA in order and the buffer is cleared (latest value wins, which is safe because counts are cumulative).
- At the switch to RECOVERING, messages still waiting in the OPC UA queue go back to the front of the buffer and are replayed over MQTT.
- When the probe declares the connection lost, the Tab5 closes the dead connection itself so that the normal reconnect loop starts.

## 5. Buffer

- Ring buffer in PSRAM. Each entry holds topic, payload, retained flag and timestamp (NTP epoch ms, `millis()` if NTP is unavailable).
- **Overflow:** drop oldest, increment a `dropped` counter shown on screen and dashboard.
- **Retained topics** (`count`, `state`): keep only the latest message per topic.
- **Event topics** (`last`, `andon`, `ack`): keep all, in order.
- **Heartbeat:** never buffered (stale on arrival).
- **In-flight messages:** messages published since the last echo are kept in a separate list. Each echo confirms the messages published before it (it carries the last `seq` used). If the connection is declared lost, the unconfirmed messages go back to the front of the buffer: they may not have reached the broker. Some will arrive twice; Node-RED drops the copies by `seq`.

## 6. Replay and `seq`

Oldest first, at most 20 messages per second, so the broker and the dashboard are not flooded. Messages published during replay are appended to the buffer so order is preserved. Replayed payloads get additive fields, `ts` (original time) and `replayed: true`; existing fields do not change. Buffered messages written to OPC UA get `ts` too.

Every data payload carries `seq`: a per-device counter that goes up by 1 per message. Node-RED uses it to detect gaps (loss) and to drop duplicates after a replay.

- **`seq` is given when a message is first sent** (over MQTT or OPC UA), not when it is created. A retained message replaced in the buffer by a newer one is never sent, so it never gets a number and does not look like a loss. **(to confirm)**
- Heartbeat, status and online/offline messages carry no `seq` (never buffered).
- `seq` restarts at 1 when the Tab5 restarts; the status topic carries a `boot` id that changes at each restart, so Node-RED knows the counter was reset.

## 7. OPC UA path

**Laptop side: go (validated Nov 19).** Node-RED hosts the OPC UA server with `node-red-contrib-opcua`:

- Endpoint `opc.tcp://<laptop>:4840/`, security None, anonymous access (lab network).
- One String variable per MQTT topic, **NodeId `ns=1;s=<full MQTT topic>`**, value = the JSON payload, exactly as it would be published over MQTT. Example: `ns=1;s=cplab/prod/st1/count` = `{"st":1,"count":42,...,"seq":118}`.
- Also `ns=1;s=cplab/rel/<devId>/status` for the status topic.
- Variables exist for `prod-st1..3`, `prod-color`, `prod-hmi`, `maint-m1..4`.
- Tested with simulated devices (Python, asyncua client): 16 s outages switch to OPC UA at 15.7–15.8 s, no message lost.

**Tab5 side: decision pending** (student A's spike on the ESP32-P4). Fallback if no OPC UA stack runs on the ESP32-P4: demonstrate the OPC UA path with the simulated devices (`simulator/device_sim.py --opc ...`) and state this limit in the presentation.

Node-RED merges MQTT and OPC UA data into one stream and tags each value with the path it came from: `MQTT`, `REPLAY` or `OPCUA`. The existing dashboards (Production, Maintenance, Supervision) read this merged stream, so they keep updating during an outage.

## 8. Visibility

The Tab5 screen shows the state, buffer fill and `dropped` count. The Node-RED dashboard page **Fiabilite** shows, per Tab5, the state and path, sent vs. received counters, missing `seq`, duplicates dropped, replayed messages, messages received over OPC UA and buffer fill, so zero loss can be shown rather than claimed.

Each Tab5 publishes `cplab/rel/<devId>/status` (retained) every second and on each state change. It is also the echo of section 3. Payload:

```json
{"id":"prod-st1","boot":"3f9c1a2b","state":"BUFFERING","path":"BUFFER","buffer":12,"capacity":500,
 "dropped":0,"seq":42,"sent":42,"replayed":0,"outage_s":6.2,"loop_max_ms":21.0,"probe":118,"ts":1763578931000}
```

`path` is `MQTT`, `BUFFER`, `OPCUA` or `REPLAY`. `seq` = last number used. `probe` = counter of status messages, used to match echoes. `loop_max_ms` = worst main loop time over the last second. `ts` = time of publication (Node-RED ignores a status older than the last one received).

The website reads from the broker's WebSocket (port 9001), so it goes blind when Mosquitto stops. Node-RED therefore exposes a WebSocket relay that does not depend on Mosquitto, and the website switches to it automatically when port 9001 drops. **Relay:** `ws://<laptop>:1880/ws/cplab`, one JSON object per message:

| `type` | When | Content |
| --- | --- | --- |
| `reliability` | Every second per Tab5 and on change | `device, state, stale, path, source, sent, received, missing, missingSeq, dup, replayed, viaOpc, viaMqtt, dropped, buffer, capacity, loopMaxMs, ts` |
| `data` | Every new value, any path | `device, topic, path, payload` (payload = what the Tab5 published) |
| `broker` | Node-RED's MQTT link changes | `connected, text` |

## 9. Broker and Node-RED configuration

A broker restart must not make Node-RED lose the messages the Tab5s replay while Node-RED reconnects:

- **Node-RED** connects with a persistent session: client id `cplab-nodered`, clean session off, keepalive 15 s, `autoUnsubscribe` on. In `settings.js`: `mqttReconnectTime: 2000` (default 15 s).
- **Mosquitto** (`mosquitto/mosquitto.conf`): `persistence true`, `persistent_client_expiration 1d`, `max_queued_messages 1000`, `queue_qos0_messages true` (the Tab5s publish with QoS 0, which Mosquitto otherwise does not keep for an absent client).

## 10. Acceptance tests

Fault injection with `scripts/outage.sh SECONDS MODE` (macOS and Linux). `stop` = broker stopped (connections close at once; restart adds 0.5–2 s, measured). `freeze` = broker paused (silent outage, exact duration). `drop` = firewall drops port 1883 (silent, can spare local traffic). Times are measured when the Tab5 status arrives in Node-RED. The dashboard buttons run the tests and write one row per Tab5 to `tests/test_matrix.csv`.

| Test | Mode | Pass criteria | Status Nov 19 (simulated Tab5s) |
| --- | --- | --- | --- |
| 10 s outage | `stop` | Zero loss, replay complete within 10 s of recovery | PASS (back in 6.4–8.4 s) |
| 14 s and 16 s outages | `freeze` | 14 s stays on MQTT, 16 s switches to OPC UA; switch within 15 s ± 2 s | PASS (16 s: switch at 15.7–15.8 s) |
| 3 × 5 s outages in a row | `stop` | No loss, no duplicate left after Node-RED's seq check | Unit tests only; no dashboard button yet |
| Outage during replay | `stop` | Order preserved, no loss | Unit tests only |
| Buffer overflow | `stop`, small `BUFFER_MAX` | Oldest dropped, `dropped` count correct | Unit tests only |
| Any scenario | | Main loop never blocks more than 100 ms; return to MQTT within 10 s of broker restart | Simulator: worst loop 26–78 ms |
| Website during a 60 s outage | `stop` | Keeps updating through the Node-RED relay and shows the OPC UA path | Relay tested (187 values over OPC UA during 16 s); website switch to do (B) |
| Loss check (all scenarios) | | Messages sent = messages received, counted by seq, replayed duplicates dropped | PASS in all runs above |

## 11. Open decisions for the group

1. Confirm `T_STABLE = 5 s` and the 20 msg/s replay rate. **Confirmed**
2. Confirm "clear buffer into OPC UA at the switch" versus holding it for MQTT replay. **Changed: switch at 15 s (was 45 s)**
3. Choose the OPC UA topology after the spike. **Laptop side go (Node-RED server). Tab5 side pending (A).**
4. Check NTP is reachable from the lab Wi-Fi. **Pending (needs the lab)**
5. Add seq to every payload and the cplab/rel/\<devId>/status topic (needed for the sent vs. received counters). **Confirmed**
6. Keep the Node-RED WebSocket relay for the website, or show the outage on the Node-RED dashboard only. **Keep the relay. Relay built.**
7. Detect silent outages with the status echo (`T_PROBE = 3 s`, t0 = last echo). **New, to confirm**
8. Give `seq` at first transmission; heartbeat, status and online/offline carry no `seq`. **New, to confirm**
9. Pause the 15 s timer during the `T_STABLE` wait. **New, to confirm**

## 12. Work split and schedule

Deadline Dec 2 (demo + presentation). Hardware: 4 h on the Tab5 in three sessions (1 h in week 2, 2 h in week 3, 1 h in week 4); everything else runs on the simulator and unit tests. Code freeze Nov 27. A = firmware, B = web and HMI, C = Node-RED and Mosquitto.

| Bloc | Dates | A | B | C |
| --- | --- | --- | --- | --- |
| 1. Setup and decisions | Nov 4–10 | Build and flash baseline; OPC UA spike on ESP32-P4 | Check website and HMI baseline; choose indicators | Mosquitto, Node-RED, simulator, fault-injection scripts; OPC UA spike on laptop **(done)** |
| 2. State machine | Nov 11–17 | 4 states, timers, non-blocking reconnect, **echo probe** | Indicator mock-ups | **Reference model in Python (`simulator/reliability.py`) and unit tests (done)** |
| 3. Buffer and replay | Nov 11–17 | PSRAM buffer, **in-flight list**, replay, ts / replayed / seq, NTP |  | mosquitto.conf: keepalive, persistence, queue size **(done)** |
| 4. OPC UA fallback | Nov 11–24 | OPC UA client on the Tab5 |  | Merge MQTT + OPC UA with a path field; WebSocket relay **(done)** |
| 5. Visibility | Nov 11–24 | Publish the status topic | Tab5 HMI banner; website state, path and source switch; sent vs. received counters | Loss and duplicate detection by seq; **existing dashboards on the merged stream (done)** |
| 6. Tests and demo | Nov 18–Dec 1 | Tests on the real Tab5 | Demo script, backup video, slides | Test matrix and CSV log **(done for 3 tests; 3 × 5 s, overflow, during-replay to add)** |

## 13. Changes since v0.4

| Section | Change | Why |
| --- | --- | --- |
| 3 | Outage start = last echo; new constants `T_PROBE`, `STATUS_PERIOD` | A failed publish never happens in a silent outage (QoS 0 data is accepted by TCP); keepalive detection takes 15–30 s, too late for the 15 s switch |
| 4 | Boot state; timer pause during `T_STABLE`; OPC UA queue back to buffer at recovery; Tab5 closes the dead connection | Cases v0.4 did not cover, found while building the reference model |
| 5 | In-flight messages | Messages accepted by TCP just before a silent outage may be lost |
| 6 | `seq` given at first send; no `seq` on heartbeat/status/online; `boot` id | Avoid false gaps from replaced retained messages; detect counter resets |
| 7 | OPC UA endpoint and NodeId convention fixed; laptop side validated | Result of the laptop spike |
| 8 | Status payload and relay message format defined | Needed by A (publisher) and B (website) |
| 9 | New section: persistent session and Mosquitto queue | Without it Node-RED lost the replayed messages after a broker restart |
| 10 | Fault-injection mode per test, measurement method, status column | `stop` adds restart time: 14 s / 16 s tests use `freeze` |
| 11 | Decisions 3 and 6 updated; 7–9 added | |
