#!/usr/bin/env python3
"""Simulated CPLab Tab5 devices with the outage handling of spec v5.

Each device publishes the same topics and payloads as the real firmware (README
topic map), plus `seq` in every data payload and `cplab/rel/<devId>/status`.
Network I/O runs in background threads (paho-mqtt, asyncua), so the main loop
never blocks: its worst gap is reported as `loop_max_ms` in the status topic.

Examples:
  python device_sim.py                                  # prod-st1 on localhost
  python device_sim.py --dev prod-st1 --dev prod-color --dev maint-m1
  python device_sim.py --opc opc.tcp://localhost:4840   # enable the OPC UA path
"""
from __future__ import annotations

import argparse
import json
import random
import socket
import sys
import threading
import time
import uuid
from collections import deque

import paho.mqtt.client as mqtt

import reliability as rel

COLORS = ["RED", "GREEN", "BLUE", "BLACK"]


# --------------------------------------------------------------- topic map --
def topics_for(dev: str) -> list[str]:
    """Data topics (without root) published by a device id."""
    if dev.startswith("prod-st"):
        return [f"prod/st{dev[-1]}/count"]
    if dev == "prod-color":
        return ["prod/color/last", "prod/color/count", "prod/st4/count"]
    if dev == "prod-hmi":
        return ["prod/andon"]
    if dev.startswith("maint-m"):
        return [f"maint/m{dev[-1]}/state", f"maint/m{dev[-1]}/ack"]
    raise ValueError(f"unknown device id {dev!r} (prod-st1..3, prod-color, prod-hmi, maint-m1..4)")


def dev_of_topic(sub: str) -> str | None:
    """Device id that publishes a data topic (topic without root), else None."""
    p = sub.split("/")
    if len(p) >= 3 and p[0] == "prod" and p[1] in ("st1", "st2", "st3"):
        return "prod-" + p[1]
    if len(p) >= 3 and p[0] == "prod" and p[1] in ("st4", "color"):
        return "prod-color"
    if len(p) == 2 and p == ["prod", "andon"]:
        return "prod-hmi"
    if len(p) >= 3 and p[0] == "maint" and p[2] in ("state", "ack"):
        return "maint-" + p[1]
    return None


# ------------------------------------------------------------- transports --
class MqttPort:
    def __init__(self, host, port, dev, root, clock, keepalive=15):
        self.clock, self.dev, self.root = clock, dev, root
        self._conn, self._since, self._disc = False, None, None
        self.link: rel.Link | None = None
        self.c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                             client_id=f"{dev}-sim-{uuid.uuid4().hex[:6]}")
        self.c.will_set(f"{root}/status/{dev}", "offline", 1, True)
        self.c.on_connect = self._on_connect
        self.c.on_disconnect = self._on_disconnect
        self.c.on_message = self._on_message
        self.c.reconnect_delay_set(2, 2)
        self.c.connect_async(host, port, keepalive)
        self.c.loop_start()

    def _on_connect(self, c, u, flags, rc, props):
        if rc.is_failure:
            return
        self._conn, self._since = True, self.clock()
        c.publish(f"{self.root}/status/{self.dev}", "online", 1, True)
        c.subscribe(self.link.status_topic, 0)

    def _on_disconnect(self, c, u, flags, rc, props):
        if self._conn:
            self._disc = self.clock()
        self._conn = False

    def _on_message(self, c, u, msg):
        if msg.topic == self.link.status_topic:
            try:
                self.link.on_echo(json.loads(msg.payload))
            except ValueError:
                pass

    # port API
    def connected(self):
        return self._conn

    def connected_since(self):
        return self._since if self._conn else None

    def disconnected_at(self):
        return self._disc

    def publish(self, topic, s, retain):
        if not self._conn:
            return False
        return self.c.publish(topic, s, 0, retain).rc == mqtt.MQTT_ERR_SUCCESS

    def set_reconnect_interval(self, s):
        self.c.reconnect_delay_set(s, s)

    def force_reconnect(self):
        """Probe timeout: the TCP link is dead but the stack has not noticed yet."""
        self._conn, self._disc = False, self.clock()
        sock = self.c.socket()
        try:
            if sock:
                sock.shutdown(socket.SHUT_RDWR)   # paho sees EOF -> reconnect loop
        except OSError:
            pass

    def close(self):
        self.c.loop_stop()
        self.c.disconnect()


class OpcPort:
    """OPC UA client in a worker thread. One String variable per MQTT topic,
    NodeId ns=<ns>;s=<full topic>, value = JSON payload (spec section 7)."""

    def __init__(self, url, ns, log):
        self.url, self.ns, self.log = url, ns, log
        self.lock = threading.Lock()
        self.q: deque = deque()
        self._status = None
        self.written = self.errors = 0
        self.connected = False
        if url:
            threading.Thread(target=self._worker, daemon=True).start()

    def enqueue(self, m, s):
        with self.lock:
            self.q.append((m, s))

    def take_back(self):
        with self.lock:
            out = [m for m, _ in self.q]
            self.q.clear()
        return out

    def pending(self):
        return len(self.q)

    def drop_oldest(self):
        with self.lock:
            return self.q.popleft()[0] if self.q else None

    def set_status(self, topic, s):
        self._status = (topic, s)

    def _worker(self):
        from asyncua import ua
        from asyncua.sync import Client
        client = None
        while True:
            if client is None:
                try:
                    client = Client(self.url, timeout=2)
                    client.connect()
                    self.connected = True
                    self.log(f"OPC UA connected to {self.url}")
                except Exception as e:  # noqa: BLE001
                    client, self.connected = None, False
                    self.log(f"OPC UA connect failed: {e.__class__.__name__}")
                    time.sleep(2)
                    continue
            with self.lock:
                item = self.q[0] if self.q else None
            status, self._status = self._status, None
            try:
                if status:
                    client.get_node(f"ns={self.ns};s={status[0]}").write_value(status[1])
                if item is None:
                    time.sleep(0.05)
                    continue
                m, s = item
                try:
                    client.get_node(f"ns={self.ns};s={m.topic}").write_value(s)
                    self.written += 1
                except ua.UaStatusCodeError as e:
                    self.errors += 1             # e.g. node missing on the server: skip it
                    self.log(f"OPC UA write {m.topic} refused: {e}")
                with self.lock:
                    if self.q and self.q[0] is item:
                        self.q.popleft()
            except Exception as e:  # noqa: BLE001  connection lost
                self.log(f"OPC UA error: {e.__class__.__name__}, reconnecting")
                try:
                    client.disconnect()
                except Exception:  # noqa: BLE001
                    pass
                client, self.connected = None, False
                time.sleep(1)


# ---------------------------------------------------------------- devices --
class Device:
    def __init__(self, dev, link: rel.Link, cycle):
        self.dev, self.link, self.cycle = dev, link, cycle
        self.next_piece = time.monotonic() + random.uniform(0, cycle)
        self.next_hb = time.monotonic()

    def step(self, now):
        if now >= self.next_hb:
            self.next_hb = now + 10
            self.link.publish(f"hb/{self.dev}", {
                "id": self.dev, "role": "sim", "group": self.dev.split("-")[0],
                "title": self.dev, "ip": "127.0.0.1", "rssi": -50,
                "up": int(now), "fw": "sim-1.0", "heap": 0})
        if now >= self.next_piece:
            self.next_piece = now + self.cycle * random.uniform(0.8, 1.2)
            self.piece(now)


class Station(Device):
    def __init__(self, dev, link, cycle):
        super().__init__(dev, link, cycle)
        self.n = int(dev[-1]) if dev[-1].isdigit() else 4   # prod-color = station 4
        self.count, self.last = 0, time.monotonic()

    def count_payload(self, now, name):
        cyc = int((now - self.last) * 1000)
        self.last = now
        return {"st": self.n, "id": self.dev, "name": name, "count": self.count,
                "cycle_ms": cyc, "cadence": round(60000 / max(cyc, 1), 1), "takt_s": 20,
                "wip_out": 0, "wip_max": 3, "kanban": "PRODUIRE", "state": "RUN"}

    def piece(self, now):
        self.count += 1
        self.link.publish(f"prod/st{self.n}/count", self.count_payload(now, f"Poste {self.n}"), True)


class ColorStation(Station):
    def __init__(self, dev, link, cycle):
        super().__init__(dev, link, cycle)
        self.n = 4
        self.colors = dict.fromkeys(COLORS + ["UNKNOWN"], 0)

    def piece(self, now):
        self.count += 1
        c = random.choice(COLORS)
        self.colors[c] += 1
        self.link.publish("prod/color/last", {"color": c, "r": 0, "g": 0, "b": 0,
                                              "dist": round(random.uniform(5, 40), 1)})
        self.link.publish("prod/color/count", {**self.colors, "total": self.count}, True)
        self.link.publish("prod/st4/count", self.count_payload(now, "Controle couleur"), True)


class Maint(Device):
    def __init__(self, dev, link, cycle):
        super().__init__(dev, link, cycle)
        self.key, self.state, self.since = dev.split("-")[1], "RUN", time.monotonic()
        self.next_state = time.monotonic()

    def step(self, now):
        super().step(now)
        if now >= self.next_state:                    # every 5 s and on change
            self.next_state = now + 5
            self.publish_state(now)

    def piece(self, now):                             # one "event" per cycle
        if random.random() < 0.2:
            self.state = random.choice(["RUN", "RUN", "OFF", "ABNORMAL"])
            self.since = now
            self.publish_state(now)
        else:
            self.link.publish(f"maint/{self.key}/ack", {"id": self.dev, "by": "sim"})

    def publish_state(self, now):
        self.link.publish(f"maint/{self.key}/state", {
            "state": self.state, "reason": "simulation", "activity": 0, "cv": 0,
            "th_off": 5, "th_high": 60, "since_s": int(now - self.since), "sim": True}, True)


class Hmi(Device):
    def piece(self, now):                             # an Andon call now and then
        if random.random() < 0.1:
            self.link.publish("prod/andon", {"from": self.dev,
                                             "type": random.choice(["supervisor", "quality"])})


def make_device(dev, link, cycle):
    topics_for(dev)                                   # validates the id
    if dev == "prod-hmi":
        return Hmi(dev, link, cycle)
    if dev == "prod-color":
        return ColorStation(dev, link, cycle)
    if dev.startswith("prod-st"):
        return Station(dev, link, cycle)
    return Maint(dev, link, cycle)


# -------------------------------------------------------------------- main --
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--root", default="cplab", help="topic root (TOPIC_ROOT)")
    ap.add_argument("--dev", action="append", help="device id, repeat for several (default prod-st1)")
    ap.add_argument("--cycle", type=float, default=1.0, help="seconds between pieces/events (default 1)")
    ap.add_argument("--opc", default="", help="OPC UA server URL, e.g. opc.tcp://localhost:4840")
    ap.add_argument("--opc-ns", type=int, default=1, help="namespace index of the nodes (default 1)")
    ap.add_argument("--t-fallback", type=float, default=15.0)
    ap.add_argument("--t-stable", type=float, default=5.0)
    ap.add_argument("--buffer-max", type=int, default=500)
    ap.add_argument("--replay-rate", type=float, default=20.0)
    ap.add_argument("--probe-timeout", type=float, default=3.0)
    ap.add_argument("--duration", type=float, default=0, help="stop after N seconds (0 = run until Ctrl-C)")
    a = ap.parse_args(argv)
    devs = a.dev or ["prod-st1"]
    cfg = rel.Config(t_fallback=a.t_fallback, t_stable=a.t_stable, buffer_max=a.buffer_max,
                     replay_rate=a.replay_rate, probe_timeout=a.probe_timeout)

    def log(msg):
        print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)

    boot = uuid.uuid4().hex[:8]
    clock, wall_ms = time.monotonic, lambda: int(time.time() * 1000)
    rigs = []
    for dev in devs:
        mq = MqttPort(a.host, a.port, dev, a.root, clock)
        opc = OpcPort(a.opc, a.opc_ns, lambda m, d=dev: log(f"[{d}] {m}"))
        link = rel.Link(dev, a.root, mq, opc, clock, wall_ms, cfg, boot=boot)
        mq.link = link
        rigs.append((dev, link, make_device(dev, link, a.cycle), opc))
        log(f"[{dev}] started (boot {boot}), broker {a.host}:{a.port}, OPC UA {a.opc or 'disabled'}")

    last_state = {dev: None for dev in devs}
    next_report = clock() + 5
    end = clock() + a.duration if a.duration else None
    try:
        while end is None or clock() < end:
            now = clock()
            for dev, link, device, opc in rigs:
                device.step(now)
                link.tick()
                if link.state != last_state[dev]:
                    last_state[dev] = link.state
                    s = link.status()
                    log(f"[{dev}] -> {link.state:12} buffer={s['buffer']} dropped={s['dropped']} "
                        f"seq={s['seq']} outage={s['outage_s']}s")
            if now >= next_report:
                next_report = now + 5
                for dev, link, _, opc in rigs:
                    s = link.status()
                    log(f"[{dev}]    {s['state']:12} path={s['path']:6} buffer={s['buffer']:3} "
                        f"dropped={s['dropped']} seq={s['seq']} replayed={s['replayed']} "
                        f"opc_written={opc.written} loop_max={s['loop_max_ms']}ms")
            time.sleep(0.02)
    except KeyboardInterrupt:
        pass
    for dev, link, _, _ in rigs:
        link.mqtt.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
