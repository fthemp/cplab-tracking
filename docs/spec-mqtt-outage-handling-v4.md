# Spec: MQTT Outage Handling with Local Buffer and OPC UA Fallback

WirelessIoT-LAB2 (CPLab) · v0.4 draft · Nov 5, 2026 · For group review, then freeze by Nov 10

## 1. Goal

When the MQTT broker becomes unreachable, a Tab5 must not lose production or maintenance data. Outage under 15 s: buffer locally and replay over MQTT. Outage of 15 s or more: switch to OPC UA. When the broker returns, go back to MQTT cleanly.

## 2. Scope

**In:** connection handling in `cplab_common.h`, local buffer, OPC UA path, connection status on the Tab5 screen and Node-RED dashboard. **Out:** Wi-Fi loss (the fallback assumes the network is up and only the broker is down), changes to Kanban or vibration logic, changes to existing topics.

## 3. Definitions

- **Outage start (t0):** the first failed publish or detected connection loss, whichever comes first.
- **Outage duration:** `millis() - t0`.
- **Constants (configurable):** ` T_FALLBACK =  ``1``5 s`, `T_STABLE = 5 s` (broker must stay reachable this long before recovery starts), `BUFFER_MAX = 500` messages (to confirm in the Nov 4-10 spike).

## 4. States and transitions

| State | Entered when | Behaviour |
| --- | --- | --- |
| CONNECTED | Broker reachable and buffer empty | Publish directly over MQTT |
| BUFFERING | Connection lost, duration < 15 s | Publishes go to the buffer; non-blocking reconnect attempt every 2 s |
| OPC\_FALLBACK | Duration reaches 15 s | Publishes go over OPC UA; MQTT reconnect attempt every 5 s continues in the background |
| RECOVERING | Broker reachable for `T_STABLE` | Replay buffer over MQTT, then return to CONNECTED |

Other transitions: BUFFERING to RECOVERING if the broker returns before 15 s. If the broker drops again during RECOVERING, return to BUFFERING with the outage timer restarted. At the switch to OPC\_FALLBACK, buffered messages are written to OPC UA in order and the buffer is cleared (latest value wins, which is safe because counts are cumulative).

## 5. Buffer

- Ring buffer in PSRAM. Each entry holds topic, payload, retained flag and timestamp (NTP epoch ms, `millis()` if NTP is unavailable).
- **Overflow:** drop oldest, increment a `dropped` counter shown on screen and dashboard.
- **Retained topics** (`count`, `state`): keep only the latest message per topic.
- **Event topics** (`last`, `andon`, `ack`): keep all, in order.
- **Heartbeat:** never buffered (stale on arrival).

## 6. Replay

Oldest first, at most 20 messages per second, so the broker and the dashboard are not flooded. Messages published during replay are appended to the buffer so order is preserved. Replayed payloads get additive fields, `ts` (original time) and `replayed: true`; existing fields do not change.

Every published payload, replayed or not, also carries `seq`: a per-device counter that goes up by 1 per message. Node-RED uses it to detect gaps (loss) and to drop duplicates after a replay.

## 7. OPC UA path

Topology is decided after the feasibility spike (target Nov 10). Preferred: Tab5 as OPC UA client writing to a server in Node-RED, with one node per MQTT topic (same names). Fallback if no stack runs on the ESP32-P4: demonstrate the OPC UA path from a simulated device and state this limit in the presentation.

Node-RED hosts the OPC UA server (port 4840). The spikes run in parallel: student A on the ESP32-P4, student C on the laptop, with a go / no-go on Nov 10. Node-RED merges MQTT and OPC UA data into one format and tags each value with the path it came from (MQTT, replayed or OPC UA).

## 8. Visibility

The Tab5 screen shows the state, buffer fill and `dropped` count. The dashboard shows the state and path of origin for each value, plus sent vs. received counters per device, so zero loss can be shown rather than claimed.

Each Tab5 also publishes `cplab/rel/<devId>/status` (retained): state, buffer fill, `dropped`, path and `seq`. It is a new topic; existing topics do not change.

The website reads from the broker's WebSocket (port 9001), so it goes blind when Mosquitto stops. Node-RED therefore exposes a WebSocket relay that does not depend on Mosquitto, and the website switches to it automatically when port 9001 drops.

## 9. Acceptance tests

| Test | Pass criteria |
| --- | --- |
| 10 s outage | Zero loss, replay complete within 10 s of recovery |
| 14 s and 16 s outages | 14 s stays on MQTT, 16 s switches to OPC UA; switch within 15 s ± 2 s |
| 3 × 5 s outages in a row | No loss, no duplicate replay |
| Outage during replay | Order preserved, no loss |
| Buffer overflow | Oldest dropped, `dropped` count correct |
| Any scenario | Main loop never blocks more than 100 ms; return to MQTT within 10 s of broker restart |
| Website during a 60 s outage | Keeps updating through the Node-RED relay and shows the OPC UA path |
| Loss check (all scenarios) | Messages sent = messages received, counted by seq, replayed duplicates dropped |

## 10. Open decisions for the group

1. Confirm `T_STABLE = 5 s` and the 20 msg/s replay rate. **C****o****nfirmed**
2. Confirm "clear buffer into OPC UA at the 45 s switch" versus holding it for MQTT replay. **C****hang****ed ****4****5s**** switc****h to 15s swi****tch**
3. Choose the OPC UA topology after the spike. **Decision pending**
4. Check NTP is reachable from the lab Wi-Fi. **Decision pending**
5. Add seq to every payload and the cplab/rel/\<devId>/status topic (needed for the sent vs. received counters). **Confirmed**
6. Keep the Node-RED WebSocket relay for the website, or show the outage on the Node-RED dashboard only. **Keep the Node-RED WebSocket relay for the website**

## 11. Work split and schedule

Deadline Dec 2 (demo + presentation). Hardware: 4 h on the Tab5 in three sessions (1 h in week 2, 2 h in week 3, 1 h in week 4); everything else runs on the simulator and unit tests. Code freeze Nov 27. A = firmware, B = web and HMI, C = Node-RED and Mosquitto.

| Bloc | Dates | A | B | C |
| --- | --- | --- | --- | --- |
| 1. Setup and decisions | Nov 4–10 | Build and flash baseline; OPC UA spike on ESP32-P4 | Check website and HMI baseline; choose indicators | Mosquitto, Node-RED, simulator, fault-injection scripts; OPC UA spike on laptop |
| 2. State machine | Nov 11–17 | 4 states, timers, non-blocking reconnect | Indicator mock-ups |  |
| 3. Buffer and replay | Nov 11–17 | PSRAM buffer, replay, ts / replayed / seq, NTP |  | mosquitto.conf: keepalive, persistence, queue size |
| 4. OPC UA fallback | Nov 11–24 | OPC UA client on the Tab5 |  | Merge MQTT + OPC UA with a path field; WebSocket relay |
| 5. Visibility | Nov 11–24 | Publish the status topic | Tab5 HMI banner; website state, path and source switch; sent vs. received counters | Loss and duplicate detection by seq |
| 6. Tests and demo | Nov 18–Dec 1 | Tests on the real Tab5 | Demo script, backup video, slides | Test matrix and CSV log |
