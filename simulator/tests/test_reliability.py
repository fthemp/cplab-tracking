"""Acceptance scenarios of spec v4 section 9, on a fake clock (runs in ~1 s).

Broker modes: "up", "stop" (connection closed, like `outage.sh stop`) and
"drop" (packets silently lost, like `outage.sh drop`).
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import reliability as r  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeMqtt:
    def __init__(self, clock):
        self.clock = clock
        self.mode = "up"
        self.conn = True
        self.since = clock()
        self.disc_at = None
        self.interval = 2.0
        self.next_try = 0.0
        self.link = None
        self.received = []          # (topic, payload dict, retain)

    # --- port API
    def connected(self):
        return self.conn

    def connected_since(self):
        return self.since if self.conn else None

    def disconnected_at(self):
        return self.disc_at

    def publish(self, topic, s, retain):
        if not self.conn:
            return False
        if self.mode == "up":
            d = json.loads(s)
            self.received.append((topic, d, retain))
            if topic == self.link.status_topic:
                self.link.on_echo(d)
        return True                 # "drop": accepted by TCP, then lost

    def set_reconnect_interval(self, s):
        self.interval = s

    def force_reconnect(self):
        self._close()

    # --- test control
    def _close(self):
        if self.conn:
            self.conn = False
            self.disc_at = self.clock()
            self.next_try = self.clock() + self.interval

    def set_mode(self, mode):
        self.mode = mode
        if mode == "stop":
            self._close()

    def step(self):
        if not self.conn and self.mode == "up" and self.clock() >= self.next_try:
            self.conn, self.since = True, self.clock()
        elif not self.conn and self.clock() >= self.next_try:
            self.next_try = self.clock() + self.interval


class FakeOpc:
    def __init__(self):
        self.up = True
        self.queue = []
        self.received = []
        self.status = None

    def enqueue(self, m, s):
        self.queue.append((m, s))

    def take_back(self):
        back = [m for m, _ in self.queue]
        self.queue.clear()
        return back

    def pending(self):
        return len(self.queue)

    def drop_oldest(self):
        return self.queue.pop(0)[0] if self.queue else None

    def set_status(self, topic, s):
        self.status = json.loads(s)

    def step(self):
        if self.up:
            for m, s in self.queue:
                self.received.append((m.topic, json.loads(s), m.retain))
            self.queue.clear()


class Rig:
    """One simulated device: a count (retained) every 1 s, an event every 0.5 s,
    a heartbeat every 10 s."""

    def __init__(self, cfg=None, opc=True):
        self.clock = Clock()
        self.mqtt = FakeMqtt(self.clock)
        self.opc = FakeOpc() if opc else None
        self.link = r.Link("prod-st1", "cplab", self.mqtt, self.opc, self.clock,
                           lambda: int(self.clock() * 1000), cfg, boot="b1")
        self.mqtt.link = self.link
        self.count = self.events = 0
        self.next_count = self.next_event = self.next_hb = self.clock()
        self.run(7)                                   # boot: wait T_STABLE, reach CONNECTED
        assert self.link.state == r.CONNECTED

    def run(self, seconds, dt=0.02):
        end = self.clock.t + seconds
        while self.clock.t < end - 1e-9:
            self.clock.t += dt
            now = self.clock.t
            if now >= self.next_count:
                self.next_count += 1.0
                self.count += 1
                self.link.publish("prod/st1/count", {"count": self.count}, retain=True)
            if now >= self.next_event:
                self.next_event += 0.5
                self.events += 1
                self.link.publish("prod/color/last", {"n": self.events})
            if now >= self.next_hb:
                self.next_hb += 10
                self.link.publish("hb/prod-st1", {"up": now})
            self.mqtt.step()
            if self.opc:
                self.opc.step()
            self.link.tick()

    def outage(self, seconds, mode="stop", after=10):
        self.mqtt.set_mode(mode)
        self.run(seconds)
        self.mqtt.set_mode("up")
        self.run(after)

    # --- checks
    def data(self):
        rows = [(t, d, "mqtt") for t, d, _ in self.mqtt.received] + \
               [(t, d, "opc") for t, d, _ in (self.opc.received if self.opc else [])]
        return [x for x in rows if "seq" in x[1] and not x[0].endswith("/status")]

    def loss(self):
        seqs = [d["seq"] for _, d, _ in self.data()]
        missing = set(range(1, self.link.seq + 1)) - set(seqs)
        return missing, len(seqs) - len(set(seqs))

    def events_received(self):
        """Event numbers after seq de-duplication, in arrival order."""
        seen, out = set(), []
        for t, d, _ in self.data():
            if t.endswith("/last") and d["seq"] not in seen:
                seen.add(d["seq"])
                out.append(d["n"])
        return out

    def states(self):
        return [s for _, s in self.link.transitions]


def test_connected_publishes_with_increasing_seq():
    rig = Rig()
    rig.run(5)
    seqs = [d["seq"] for _, d, _ in rig.data()]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert rig.loss() == (set(), 0)


@pytest.mark.parametrize("mode", ["stop", "drop"])
def test_10s_outage_zero_loss_replay_within_10s(mode):
    rig = Rig()
    rig.mqtt.set_mode(mode)
    rig.run(10)
    rig.mqtt.set_mode("up")
    t_back = rig.clock.t
    rig.run(15)
    assert r.OPC_FALLBACK not in rig.states()
    assert rig.link.state == r.CONNECTED
    t_connected = [t for t, s in rig.link.transitions if s == r.CONNECTED][-1]
    assert t_connected - t_back <= 10
    missing, _ = rig.loss()
    assert missing == set()
    assert rig.events_received() == list(range(1, rig.events + 1))
    assert any(d.get("replayed") for _, d, _ in rig.data())


def test_14s_stays_on_mqtt_16s_switches_to_opc():
    rig = Rig()
    rig.outage(14, "stop", after=12)
    assert r.OPC_FALLBACK not in rig.states()

    rig = Rig()
    rig.mqtt.set_mode("stop")
    t0 = rig.clock.t
    rig.run(16)
    switch = [t for t, s in rig.link.transitions if s == r.OPC_FALLBACK]
    assert switch and abs(switch[0] - t0 - 15) <= 2
    rig.mqtt.set_mode("up")
    rig.run(15)
    assert rig.link.state == r.CONNECTED
    assert rig.loss()[0] == set()
    assert any(p == "opc" for _, _, p in rig.data())
    # MQTT and OPC UA arrive on two paths: check completeness, not arrival order
    assert sorted(rig.events_received()) == list(range(1, rig.events + 1))


def test_silent_drop_switches_to_opc_on_time():
    rig = Rig()
    rig.mqtt.set_mode("drop")
    t0 = rig.clock.t
    rig.run(18)
    switch = [t for t, s in rig.link.transitions if s == r.OPC_FALLBACK]
    assert switch and abs(switch[0] - t0 - 15) <= 2
    rig.mqtt.set_mode("up")
    rig.run(15)
    assert rig.loss()[0] == set()


def test_three_5s_outages_in_a_row():
    rig = Rig()
    for _ in range(3):
        rig.outage(5, "stop", after=5)
    rig.run(10)
    assert r.OPC_FALLBACK not in rig.states()
    assert rig.loss()[0] == set()
    assert rig.events_received() == list(range(1, rig.events + 1))


def test_outage_during_replay_keeps_order():
    rig = Rig()
    rig.link.cfg.replay_rate = 4.0                   # slow replay so we can cut it
    rig.outage(10, "stop", after=6)                  # T_STABLE 5 s, then replay starts
    assert rig.link.state == r.RECOVERING
    rig.outage(5, "stop", after=60)
    assert rig.link.state == r.CONNECTED
    assert rig.loss()[0] == set()
    assert rig.events_received() == list(range(1, rig.events + 1))


def test_buffer_overflow_drops_oldest():
    cfg = r.Config(buffer_max=10, t_fallback=999)
    rig = Rig(cfg)
    rig.mqtt.set_mode("stop")
    rig.run(10)                                       # ~20 events + counts > 10
    assert len(rig.link.buffer) == 10
    newest = [m.payload["n"] for m in rig.link.buffer.q if m.topic.endswith("/last")]
    assert newest[-1] == rig.events                  # newest kept, oldest dropped
    rig.mqtt.set_mode("up")
    rig.run(10)                                      # still dropping during T_STABLE
    dropped = rig.link.buffer.dropped
    got = rig.events_received()
    lost_events = rig.events - len(got)
    assert dropped > 0 and lost_events > 0
    lost_seq = rig.loss()[0]                         # messages that had a seq and were dropped
    assert lost_events <= dropped                    # every lost event is counted
    assert len(lost_seq) <= dropped
    assert got == sorted(got)


def test_retained_kept_latest_only_and_heartbeat_not_buffered():
    rig = Rig(r.Config(t_fallback=999))
    rig.mqtt.set_mode("stop")
    rig.run(12)
    topics = [m.topic for m in rig.link.buffer.q]
    assert topics.count("cplab/prod/st1/count") <= 2  # latest + maybe one already in flight
    assert not any("/hb/" in t for t in topics)


def test_replay_rate_limited():
    rig = Rig(r.Config(t_fallback=999))
    rig.outage(12, "stop", after=0)
    rig.run(5)                                       # T_STABLE
    start = rig.clock.t
    before = rig.link.replayed
    rig.run(1)
    assert rig.link.replayed - before <= 21
    assert rig.clock.t - start <= 1.01


def test_status_topic_and_loop_time():
    rig = Rig()
    st = [d for t, d, _ in rig.mqtt.received if t == "cplab/rel/prod-st1/status"][-1]
    for k in ("state", "buffer", "dropped", "path", "seq"):
        assert k in st
    assert "seq" not in [k for k in st if k == "seq" and st.get("replayed") is True]
    assert rig.link.loop_max_ms < 100
