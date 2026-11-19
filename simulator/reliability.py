"""Reliability layer of spec v5 (MQTT outage handling), as plain Python.

This module has no network code: the MQTT and OPC UA transports are passed in,
so the logic can be unit-tested with a fake clock (tests/) and reused by the
device simulator (device_sim.py). It is also meant as a readable model for the
firmware version in cplab_common.h.

States (spec section 4):
  CONNECTED     broker reachable, buffer empty -> publish directly over MQTT
  BUFFERING     outage < T_FALLBACK            -> publishes go to the buffer
  OPC_FALLBACK  outage >= T_FALLBACK           -> publishes go over OPC UA
  RECOVERING    broker reachable for T_STABLE  -> replay the buffer, then CONNECTED

Choices made here, now written in spec v5 (sections 3 to 6; some still to confirm):
  * Outage detection. A silent outage (packets dropped, no TCP reset) is not seen
    by a failed publish: the TCP stack accepts the data. So the device publishes
    its retained status topic every second and subscribes to it (probe). No echo
    for PROBE_TIMEOUT -> connection treated as lost, t0 = time of the last echo.
  * Messages published since the last echo are kept ("in flight") and replayed
    if the connection is lost: they may have been lost. Node-RED drops the
    duplicates by seq.
  * seq is assigned when a message is first transmitted (MQTT or OPC UA), not
    when it is created. Retained messages replaced in the buffer by a newer one
    never get a seq, so they do not show up as gaps. A message dropped by buffer
    overflow is counted in `dropped`.
  * Heartbeat, status and online/offline messages carry no seq (never buffered).
  * The fallback timer is paused while the broker is reachable again and the
    device waits for T_STABLE; if the broker drops again, the outage restarts.
"""
from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field

CONNECTED = "CONNECTED"
BUFFERING = "BUFFERING"
OPC_FALLBACK = "OPC_FALLBACK"
RECOVERING = "RECOVERING"

PATH = {CONNECTED: "MQTT", BUFFERING: "BUFFER", OPC_FALLBACK: "OPCUA", RECOVERING: "REPLAY"}


@dataclass
class Config:
    t_fallback: float = 15.0        # s, outage duration that triggers OPC UA
    t_stable: float = 5.0           # s, broker reachable this long before replay
    buffer_max: int = 500           # messages (buffer + OPC UA queue)
    replay_rate: float = 20.0       # messages per second
    reconnect_buffering: float = 2.0
    reconnect_fallback: float = 5.0
    probe_timeout: float = 3.0      # s without status echo -> connection lost
    status_period: float = 1.0      # s


@dataclass
class Msg:
    topic: str          # full topic, e.g. cplab/prod/st1/count
    payload: dict
    retain: bool
    ts_ms: int          # original time (epoch ms)
    seq: int | None = None


class Buffer:
    """Ring buffer: retained topics keep only their latest value, events keep all."""

    def __init__(self, cap: int):
        self.cap = cap
        self.q: deque[Msg] = deque()
        self.dropped = 0

    def __len__(self):
        return len(self.q)

    def push(self, m: Msg):
        if m.retain and m.seq is None:
            # keep only the latest value; never remove a message that already has
            # a seq (it may have been half-sent, removing it would make a gap)
            self.q = deque(x for x in self.q if not (x.topic == m.topic and x.seq is None))
        self.q.append(m)
        self._trim()

    def push_front(self, msgs: list[Msg]):
        """Put older messages (oldest first) back in front of the buffer."""
        self.q.extendleft(reversed(msgs))
        self._trim()

    def popleft(self) -> Msg:
        return self.q.popleft()

    def take_all(self) -> list[Msg]:
        out = list(self.q)
        self.q.clear()
        return out

    def _trim(self):
        while len(self.q) > self.cap:
            # Drop the oldest message that has no seq yet. A message with a seq was
            # sent before the link was lost and put back here unconfirmed: dropping
            # it would show in Node-RED as a gap, the sign of an unexplained loss.
            # Only if every message has a seq (more in flight than the capacity),
            # drop the oldest one anyway; it is still counted in `dropped`.
            for i, m in enumerate(self.q):
                if m.seq is None:
                    del self.q[i]
                    break
            else:
                self.q.popleft()
            self.dropped += 1


class Link:
    """One device's connection manager. Call publish() and tick() from one thread.

    mqtt port:  connected() -> bool, connected_since() -> float | None,
                disconnected_at() -> float | None, publish(topic, str, retain) -> bool,
                set_reconnect_interval(s), force_reconnect()
    opc port:   enqueue(msg, str), take_back() -> list[Msg], pending() -> int,
                drop_oldest() -> Msg | None, set_status(topic, str)
    """

    def __init__(self, dev_id, root, mqtt, opc, clock, wall_ms, cfg: Config | None = None, boot=""):
        self.dev_id, self.root = dev_id, root
        self.mqtt, self.opc, self.clock, self.wall_ms = mqtt, opc, clock, wall_ms
        self.cfg = cfg or Config()
        self.boot = boot
        self.buffer = Buffer(self.cfg.buffer_max)
        self.inflight: deque[Msg] = deque()
        self.seq = 0
        self.replayed = 0
        self.status_topic = f"{root}/rel/{dev_id}/status"
        now = clock()
        self.state = BUFFERING          # until the broker has been reachable T_STABLE
        self.t0 = now
        self.transitions: list[tuple[float, str]] = [(now, BUFFERING)]
        self._was_connected = False
        self._next_replay = now
        self._next_status = now
        self._probe_nonce = 0
        self._await: tuple[int, float] | None = None     # first probe not echoed yet
        self._last_echo: float | None = None
        self._echoes: deque[dict] = deque()               # filled by the MQTT thread
        self._last_tick = now
        self.loop_max_ms = 0.0
        self._loop_max_window = 0.0

    # ------------------------------------------------------------------ API --
    def publish(self, sub: str, payload: dict, retain: bool = False):
        now = self.clock()
        m = Msg(f"{self.root}/{sub}", dict(payload), retain, self.wall_ms())
        heartbeat = sub.startswith("hb/")
        if heartbeat:
            if self.state == CONNECTED:
                self.mqtt.publish(m.topic, json.dumps(m.payload), retain)
            return                                           # never buffered
        if self.state == CONNECTED and not len(self.buffer):
            self._assign(m)
            if self.mqtt.publish(m.topic, self._encode(m), retain):
                self.inflight.append(m)
                return
            self._lose(now, self.mqtt.disconnected_at() or now)
            self.buffer.push(m)                              # keeps its seq
        elif self.state == OPC_FALLBACK:
            self._to_opc(m, late=False)
        else:
            self.buffer.push(m)

    def on_echo(self, payload: dict):
        """Called from the MQTT thread when our own status message comes back."""
        self._echoes.append(payload)

    def tick(self):
        now = self.clock()
        gap = (now - self._last_tick) * 1000
        self._last_tick = now
        self._loop_max_window = max(self._loop_max_window, gap)
        self._process_echoes(now)

        connected = self.mqtt.connected()
        if connected and self._await and now - self._await[1] > self.cfg.probe_timeout:
            t0 = self._last_echo if self._last_echo is not None else self._await[1]
            self._await = None
            self.mqtt.force_reconnect()
            connected = False
            if self.state in (CONNECTED, RECOVERING):
                self._lose(now, t0)
            else:
                self.t0 = now                    # dropped again while waiting T_STABLE
            self._was_connected = False          # t0 is set, do not reset it below

        if self.state == CONNECTED:
            if not connected:
                self._lose(now, self.mqtt.disconnected_at() or now)
        elif self.state in (BUFFERING, OPC_FALLBACK):
            if connected:
                since = self.mqtt.connected_since()
                if since is not None and now - since >= self.cfg.t_stable:
                    self._enter_recovering(now)
            else:
                if self._was_connected:          # broker came back, then dropped again
                    self.t0 = now
                if self.state == BUFFERING and now - self.t0 >= self.cfg.t_fallback:
                    self._enter_fallback(now)
        if self.state == RECOVERING:
            if not connected:
                self._set_state(now, BUFFERING)
                self.t0 = now
            else:
                self._replay(now)
        self._was_connected = connected

        if self.opc is not None:
            while self.opc.pending() + len(self.buffer) > self.cfg.buffer_max:
                if self.opc.drop_oldest() is None:
                    break
                self.buffer.dropped += 1

        if now >= self._next_status:
            self._next_status = now + self.cfg.status_period
            self._send_status(now)
            self.loop_max_ms = self._loop_max_window
            self._loop_max_window = 0.0

    # ------------------------------------------------------------- helpers --
    def status(self, now=None) -> dict:
        now = self.clock() if now is None else now
        fill = len(self.buffer) + (self.opc.pending() if self.opc is not None else 0)
        return {
            "id": self.dev_id, "boot": self.boot, "state": self.state, "path": PATH[self.state],
            "buffer": fill, "capacity": self.cfg.buffer_max, "dropped": self.buffer.dropped,
            "seq": self.seq, "sent": self.seq, "replayed": self.replayed,
            "outage_s": round(now - self.t0, 1) if self.state != CONNECTED else 0,
            "loop_max_ms": round(self.loop_max_ms, 1), "probe": self._probe_nonce,
            "ts": self.wall_ms(),
        }

    def _send_status(self, now):
        if self.mqtt.connected():
            self._probe_nonce += 1
            if self._await is None:
                self._await = (self._probe_nonce, now)
        s = json.dumps(self.status(now))
        if self.mqtt.connected():
            self.mqtt.publish(self.status_topic, s, True)
        if self.state == OPC_FALLBACK and self.opc is not None:
            self.opc.set_status(self.status_topic, s)

    def _process_echoes(self, now):
        while self._echoes:
            e = self._echoes.popleft()
            if e.get("boot") != self.boot:
                continue                              # retained status of an older run
            if self._await and e.get("probe", -1) >= self._await[0]:
                self._await = None
                self._last_echo = now
                confirmed = e.get("seq", 0)
                while self.inflight and self.inflight[0].seq <= confirmed:
                    self.inflight.popleft()

    def _assign(self, m: Msg):
        if m.seq is None:
            self.seq += 1
            m.seq = self.seq

    @staticmethod
    def _encode(m: Msg, replayed=False, late=False) -> str:
        d = dict(m.payload)
        d["seq"] = m.seq
        if replayed or late:
            d["ts"] = m.ts_ms                      # additive fields only (spec 6)
        if replayed:
            d["replayed"] = True
        return json.dumps(d)

    def _set_state(self, now, st):
        if st != self.state:
            self.state = st
            self.transitions.append((now, st))
            self._next_status = now                # publish the change at once

    def _lose(self, now, t0):
        self._set_state(now, BUFFERING)
        self.t0 = min(t0, now)
        self._await = None
        self.mqtt.set_reconnect_interval(self.cfg.reconnect_buffering)
        if self.inflight:                          # may not have reached the broker
            self.buffer.push_front(list(self.inflight))
            self.inflight.clear()

    def _to_opc(self, m: Msg, late: bool):
        self._assign(m)
        self.opc.enqueue(m, self._encode(m, late=late))

    def _enter_fallback(self, now):
        self._set_state(now, OPC_FALLBACK)
        self.mqtt.set_reconnect_interval(self.cfg.reconnect_fallback)
        for m in self.buffer.take_all():           # in order; latest value wins
            self._to_opc(m, late=True)

    def _enter_recovering(self, now):
        self._set_state(now, RECOVERING)
        self.mqtt.set_reconnect_interval(self.cfg.reconnect_buffering)
        if self.opc is not None:
            back = self.opc.take_back()            # not written to OPC UA yet
            if back:
                self.buffer.push_front(back)
        self._next_replay = now

    def _replay(self, now):
        step = 1.0 / self.cfg.replay_rate
        if self._next_replay < now - step:
            self._next_replay = now
        while len(self.buffer) and now >= self._next_replay:
            m = self.buffer.popleft()
            self._assign(m)
            if not self.mqtt.publish(m.topic, self._encode(m, replayed=True), m.retain):
                self.buffer.push_front([m])
                self._lose(now, now)
                return
            self.inflight.append(m)
            self.replayed += 1
            self._next_replay += step
        if not len(self.buffer):
            self._set_state(now, CONNECTED)
