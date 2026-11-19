#!/usr/bin/env python3
"""Stand-in for the Node-RED OPC UA server (port 4840) until that flow exists.

Creates one writable String variable per MQTT topic of the given devices,
NodeId ns=1;s=<full topic> (e.g. ns=1;s=cplab/prod/st1/count), plus the status
topic cplab/rel/<devId>/status. Prints every value written by a device.

  python opcua_test_server.py --dev prod-st1 --dev prod-color
"""
from __future__ import annotations

import argparse
import time

from asyncua import ua
from asyncua.sync import Server

from device_sim import topics_for


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=4840)
    ap.add_argument("--root", default="cplab")
    ap.add_argument("--dev", action="append", help="device ids (default prod-st1)")
    ap.add_argument("--quiet", action="store_true", help="do not print each write")
    a = ap.parse_args(argv)
    devs = a.dev or ["prod-st1"]

    server = Server()
    server.set_endpoint(f"opc.tcp://0.0.0.0:{a.port}/")
    server.set_server_name("CPLab OPC UA test server")
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
    folder = server.nodes.objects.add_folder(ua.NodeId("cplab", 1), "cplab")
    nodes = []
    for dev in devs:
        for t in topics_for(dev) + [f"rel/{dev}/status"]:
            name = f"{a.root}/{t}"
            v = folder.add_variable(ua.NodeId(name, 1), name, "", ua.VariantType.String)
            v.set_writable()
            nodes.append(v)

    class Printer:
        def datachange_notification(self, node, val, data):
            if val and not a.quiet:
                print(f"{time.strftime('%H:%M:%S')} OPC UA write {node.nodeid.Identifier}: {val[:120]}",
                      flush=True)

    server.start()
    try:
        sub = server.create_subscription(50, Printer())
        sub.subscribe_data_change(nodes, queuesize=1000)
        print(f"OPC UA test server on opc.tcp://localhost:{a.port}/ with {len(nodes)} nodes (ns=1)", flush=True)
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()


if __name__ == "__main__":
    main()
