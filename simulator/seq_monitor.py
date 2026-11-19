#!/usr/bin/env python3
"""Loss and duplicate check by seq (spec section 9, "Loss check").

Listens to MQTT (and optionally OPC UA), and for each device compares:
  sent      = `seq` in cplab/rel/<devId>/status (messages the device numbered)
  received  = distinct seq values seen on MQTT + OPC UA
  missing   = seq values never received (from the first one seen)
  dup       = copies dropped (replays of messages that had already arrived)
This is what the Node-RED loss check must do; use it to validate the devices
before that flow exists, and to cross-check it afterwards.

  python seq_monitor.py                                   # MQTT only, until Ctrl-C
  python seq_monitor.py --opc opc.tcp://localhost:4840 --dev prod-st1
  python seq_monitor.py --duration 60 --csv ../tests/seq_results.csv --label "10 s stop"
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import threading
import time

import paho.mqtt.client as mqtt

from device_sim import dev_of_topic, topics_for


class Stats:
    def __init__(self):
        self.boot = None
        self.reset()

    def reset(self):
        self.seqs: set[int] = set()
        self.first = None
        self.dup = self.replayed = self.via_opc = 0
        self.sent = 0
        self.state = self.path = "?"
        self.dropped = 0
        self.states: list[str] = []

    def data(self, seq, replayed, path):
        if seq in self.seqs:
            self.dup += 1
            return
        self.seqs.add(seq)
        self.first = seq if self.first is None else min(self.first, seq)
        self.replayed += bool(replayed)
        self.via_opc += path == "opc"

    def missing(self):
        if self.first is None:
            return []
        top = max(self.sent, max(self.seqs))
        return [s for s in range(self.first, top + 1) if s not in self.seqs]


class Monitor:
    def __init__(self, root):
        self.root = root
        self.lock = threading.Lock()
        self.dev: dict[str, Stats] = {}

    def get(self, dev):
        return self.dev.setdefault(dev, Stats())

    def on_status(self, dev, d):
        with self.lock:
            st = self.get(dev)
            if st.boot != d.get("boot"):            # device restarted: seq restarts at 1
                st.boot = d.get("boot")
                st.reset()
            st.sent = max(st.sent, d.get("seq", 0))
            st.dropped = d.get("dropped", 0)
            if d.get("state") != st.state:
                st.states.append(d.get("state"))
            st.state, st.path = d.get("state", "?"), d.get("path", "?")

    def on_data(self, sub, d, path):
        dev = dev_of_topic(sub)
        if dev is None or "seq" not in d:
            return
        with self.lock:
            self.get(dev).data(d["seq"], d.get("replayed"), path)

    def table(self):
        with self.lock:
            rows = []
            for dev in sorted(self.dev):
                s = self.dev[dev]
                m = s.missing()
                sent = max([s.sent] + list(s.seqs))     # status lags the data by up to 1 s
                rows.append(dict(device=dev, state=s.state, path=s.path, sent=sent,
                                 received=len(s.seqs), missing=len(m), dup=s.dup,
                                 replayed=s.replayed, via_opc=s.via_opc, dropped=s.dropped,
                                 states=">".join(s.states), missing_seq=" ".join(map(str, m[:20]))))
            return rows


def start_opc(url, ns, root, devs, mon: Monitor, log):
    from asyncua.sync import Client

    class Handler:
        def __init__(self):
            self.seen = set()

        def datachange_notification(self, node, val, data):
            nid = node.nodeid.Identifier
            if nid not in self.seen:                 # initial value = stale, skip it
                self.seen.add(nid)
                return
            try:
                d = json.loads(val)
            except (TypeError, ValueError):
                return
            sub = nid[len(root) + 1:]
            if sub.startswith("rel/"):
                mon.on_status(sub.split("/")[1], d)
            else:
                mon.on_data(sub, d, "opc")

    def run():
        while True:
            try:
                c = Client(url, timeout=2)
                c.connect()
                h = Handler()
                sub = c.create_subscription(100, h)
                nodes = [c.get_node(f"ns={ns};s={root}/{t}") for dv in devs for t in topics_for(dv)]
                nodes += [c.get_node(f"ns={ns};s={root}/rel/{dv}/status") for dv in devs]
                sub.subscribe_data_change(nodes, queuesize=1000)
                log(f"OPC UA monitoring {len(nodes)} nodes on {url}")
                while True:
                    time.sleep(1)
                    c.get_namespace_array()          # raises if the server went away
            except Exception as e:  # noqa: BLE001
                log(f"OPC UA: {e.__class__.__name__}, retry in 2 s")
                time.sleep(2)

    threading.Thread(target=run, daemon=True).start()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--root", default="cplab")
    ap.add_argument("--opc", default="", help="OPC UA server URL (also count values written there)")
    ap.add_argument("--opc-ns", type=int, default=1)
    ap.add_argument("--dev", action="append", help="devices to watch on OPC UA (default prod-st1)")
    ap.add_argument("--duration", type=float, default=0, help="stop after N seconds")
    ap.add_argument("--csv", default="", help="append the final result to this CSV file")
    ap.add_argument("--label", default="", help="test name written in the CSV")
    a = ap.parse_args(argv)

    def log(msg):
        print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)

    mon = Monitor(a.root)
    pre = a.root + "/"

    def on_message(c, u, msg):
        if msg.retain:            # delivered from the broker's store, not live: skip
            return
        sub = msg.topic[len(pre):]
        try:
            d = json.loads(msg.payload)
        except ValueError:
            return
        if not isinstance(d, dict):
            return
        if sub.startswith("rel/") and sub.endswith("/status"):
            mon.on_status(sub.split("/")[1], d)
        else:
            mon.on_data(sub, d, "mqtt")

    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"seq-monitor-{os.getpid()}")
    c.on_connect = lambda c, u, f, rc, p: (c.subscribe(pre + "#", 0), log("MQTT connected"))
    c.on_disconnect = lambda c, u, f, rc, p: log("MQTT disconnected (broker down?)")
    c.on_message = on_message
    c.reconnect_delay_set(1, 1)
    c.connect_async(a.host, a.port, 15)
    c.loop_start()
    if a.opc:
        start_opc(a.opc, a.opc_ns, a.root, a.dev or ["prod-st1"], mon, log)

    end = time.monotonic() + a.duration if a.duration else None
    try:
        while end is None or time.monotonic() < end:
            time.sleep(min(5, max(0.1, end - time.monotonic())) if end else 5)
            for r in mon.table():
                log("{device:11} {state:12} {path:6} sent={sent} received={received} "
                    "missing={missing} dup={dup} replayed={replayed} via_opc={via_opc} "
                    "dropped={dropped}".format(**r))
    except KeyboardInterrupt:
        pass
    c.loop_stop()
    rows = mon.table()
    print("\nRESULT")
    for r in rows:
        ok = r["missing"] == 0 and r["sent"] == r["received"]
        print(f"  {r['device']:11} {'PASS' if ok else 'FAIL'}  sent={r['sent']} received={r['received']} "
              f"missing={r['missing']} {r['missing_seq']} dup_dropped={r['dup']} replayed={r['replayed']} "
              f"via_opc={r['via_opc']} buffer_dropped={r['dropped']} states={r['states']}")
    if a.csv:
        new = not os.path.exists(a.csv)
        with open(a.csv, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["time_utc", "label", "device", "sent", "received", "missing", "dup_dropped",
                            "replayed", "via_opc", "buffer_dropped", "states", "missing_seq"])
            for r in rows:
                w.writerow([time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), a.label, r["device"],
                            r["sent"], r["received"], r["missing"], r["dup"], r["replayed"],
                            r["via_opc"], r["dropped"], r["states"], r["missing_seq"]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
