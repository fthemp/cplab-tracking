# Node-RED flows

`cplab_flows.json` has 5 tabs: Production, Maintenance, Supervision, Simulator (the baseline)
and **CPLab Reliability**, the outage handling of the spec
([docs/spec-mqtt-outage-handling-v5.md](../docs/spec-mqtt-outage-handling-v5.md)).
Dashboard: `http://<laptop>:1880/dashboard`, page **Fiabilite** (`/reliability`).

## Setup

1. **Palette** (Manage palette > Install): `@flowfuse/node-red-dashboard`, `node-red-contrib-opcua`.
2. **settings.js** (in `~/.node-red`), then restart Node-RED:
   ```js
   mqttReconnectTime: 2000,      // default 15000: Node-RED would come back 15 s after the broker
   ```
3. **Mosquitto**: use [mosquitto/mosquitto.conf](../mosquitto/mosquitto.conf) (it queues messages
   for Node-RED while it reconnects, see below).
4. **Import** `cplab_flows.json`, deploy.
5. **Outage test buttons** only: tell Node-RED where this repo is. Double-click the
   "CPLab Reliability" tab > Environment variables > add `CPLAB_REPO` = the full path of the
   `cplab-tracking` folder. Or start Node-RED with `CPLAB_REPO=/path/to/cplab-tracking node-red`.

## What the Reliability tab does

| Part | Spec | What it does |
|---|---|---|
| OPC UA server :4840 | 7 | Creates one String variable per MQTT topic, `ns=1;s=<full topic>` (e.g. `ns=1;s=cplab/prod/st1/count`), value = the JSON payload, plus `ns=1;s=cplab/rel/<devId>/status`. A Tab5 in `OPC_FALLBACK` writes there. Endpoint `opc.tcp://<laptop>:4840/`, anonymous, no security. |
| seq check | 6, 8, 9 | Merges MQTT and OPC UA into one stream, tags each value `MQTT`, `REPLAY` or `OPCUA`, drops copies already received (by `seq`), counts per Tab5: sent, received, missing, duplicates, replayed, via OPC UA, dropped by the Tab5 buffer. Output "CPLab merged data" (link out) for other tabs. |
| WebSocket relay | 8 | `ws://<laptop>:1880/ws/cplab`, served by Node-RED itself, so it keeps working when Mosquitto is down. |
| Outage tests | 9 | Dashboard buttons run `scripts/outage.sh`, wait for the Tab5s to come back, check the criteria and add one row per Tab5 to `tests/test_matrix.csv`. |

### Pass criteria used by the test buttons

No `seq` missing, nothing dropped by the Tab5 buffer, switch to OPC UA only for outages of 15 s
or more and then 15 ± 2 s after the start, back on MQTT within the return limit after the
broker's return: 10 s, or 5 s (`T_STABLE`) + reconnect interval (2 s, 5 s after OPC UA) +
2 s (reconnection overhead) + n ÷ 20 s if longer, n = messages replayed, duplicates included (spec v5 section 10).
Times are measured when the Tab5 status arrives in Node-RED.

Tests with several outages (spec v5 section 10):

| Button | What it does | Extra criterion |
|---|---|---|
| 3 x 5 s (stop) | Three 5 s stops, 3 s apart (less than T_STABLE, so the Tab5s never settle in between) | none; times count from the first cut and the last restore |
| Coupure pendant le rejeu | 14 s freeze, then a 5 s freeze as soon as a Tab5 reports `RECOVERING` (timeout 30 s) | the Tab5 was `RECOVERING` when the 2nd outage started |
| Debordement (12 s stop) | One 12 s stop (14 s + the restart time of `stop` would reach 15 s and switch to OPC UA) | the buffer overflowed: `dropped` > 0, still no `seq` missing |

`INCONCLUSIVE` means nothing went wrong but the test did not create the situation it checks:
a Tab5 whose buffer never filled, or one that had already finished replaying. For the overflow
test, start one simulated Tab5 with a small buffer (`--buffer-max 20`, see
[simulator/README.md](../simulator/README.md)); with the real 500 messages, nothing overflows.

## The baseline tabs read the merged stream

Production, Maintenance and Supervision no longer have their own `mqtt in` nodes. Each tab has
one `link in` ("CPLab merged data") fed by the Reliability tab, and each former `mqtt in` is now
a `switch` node keeping only its old topic (same position and wires). So these dashboards keep
updating while the Tab5s are on OPC UA and Mosquitto is down: tested with a 40 s stop, the
station counts kept rising on the Production page.

Not covered: commands sent **to** the Tab5s (OF, messages, actions) still go through MQTT, so
they don't reach them while the broker is down. Heartbeats are never buffered (spec), so the
Supervision page shows the Tab5s as silent during an outage: that's expected.

On Linux, see [docs/linux-setup.md](../docs/linux-setup.md).

## Relay messages (for the website, team B)

One JSON object per WebSocket message:

```js
// every second per Tab5, and on each change
{type:"reliability", device:"prod-st1", state:"OPC_FALLBACK", stale:false, path:"OPCUA",
 source:"OPC UA (Node-RED)", sent:120, received:118, missing:2, missingSeq:[119,120],
 dup:3, replayed:0, viaOpc:22, viaMqtt:96, dropped:0, buffer:0, capacity:500, loopMaxMs:21, ts:...}
// every new value, whatever the path (payload = what the Tab5 published)
{type:"data", device:"prod-st1", topic:"cplab/prod/st1/count", path:"MQTT"|"REPLAY"|"OPCUA", payload:{...}, ts:...}
// Node-RED's own MQTT link
{type:"broker", connected:false, text:"disconnected", ts:...}
```

`Festo_Command_Center.html` already understands `type:"reliability"`, but it shows one device
at a time: it should keep one entry per `device`. To keep the line view updating during an
outage, it can feed `type:"data"` messages to the same handler as MQTT messages.

## Why Node-RED uses a persistent MQTT session

Mosquitto sends a QoS 0 message only to clients connected at that moment. After a broker
restart the Tab5s reconnect and replay their buffer, and if Node-RED is not reconnected yet,
those messages are lost for it. So the broker config node uses client id `cplab-nodered`,
clean session off, keepalive 15 s, and `mosquitto.conf` has `queue_qos0_messages true`:
the broker keeps Node-RED's messages while it reconnects.

Mosquitto sends one copy per matching subscription. Node-RED now has a single subscription
(`cplab/#`), so each message arrives once. `autoUnsubscribe` stays on: when nodes are removed
and redeployed, their subscriptions leave the session instead of causing extra copies.
If an old session still holds subscriptions (e.g. after testing older flows), clear it once,
with Node-RED stopped: `mosquitto_sub -i cplab-nodered -t cplab/none -W 1`.
