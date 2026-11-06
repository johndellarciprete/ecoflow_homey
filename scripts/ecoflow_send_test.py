#!/usr/bin/env python3
"""
TEST: send a circuit ON/OFF command to the EcoFlow Smart Home Panel 3.

This actively sends our reverse-engineered command to verify (a) the command
format works when WE publish it, and (b) circuit number -> control-id mapping.

⚠ This physically switches a real circuit. Pick a SAFE circuit (a lamp / unused
outlet — never the fridge, HVAC, well pump, medical, or anything that matters).

USAGE (PowerShell):
    python ecoflow_send_test.py johndellarciprete@gmail.com --circuit 1

It turns the circuit OFF, waits 4s, turns it back ON, and prints the panel's
acks. Watch that circuit (or the EcoFlow app) to confirm the RIGHT one toggled.
"""

from __future__ import annotations

import base64
import getpass
import json
import os
import ssl
import sys
import time
import urllib.request

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from ecoflow.control import build_circuit_command, control_id_for  # noqa: E402

API = "api.ecoflow.com"
PANEL_SN = "HR63ZA1AVJ1E0369"


def _post(url, body, headers):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _get(url, headers):
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
        return json.loads(r.read().decode())


def main():
    args = sys.argv[1:]
    circuit = None
    if "--circuit" in args:
        i = args.index("--circuit"); circuit = int(args[i + 1]); del args[i:i + 2]
    sn = PANEL_SN
    if "--sn" in args:
        i = args.index("--sn"); sn = args[i + 1].strip().upper(); del args[i:i + 2]
    if circuit is None:
        raise SystemExit("Specify --circuit N (a SAFE circuit to test).")
    email = args[0] if args else os.environ.get("ECOFLOW_EMAIL") or input("EcoFlow email: ").strip()
    password = os.environ.get("ECOFLOW_PASSWORD") or getpass.getpass("EcoFlow password (hidden): ")

    import paho.mqtt.client as mqtt

    resp = _post(f"https://{API}/auth/login",
                 {"email": email, "password": base64.b64encode(password.encode()).decode(),
                  "scene": "IOT_APP", "userType": "ECOFLOW"},
                 {"lang": "en_US", "Content-Type": "application/json"})
    d = resp["data"]; token = d["token"]; user_id = d["user"]["userId"]
    cert = _get(f"https://{API}/iot-auth/app/certification?userId={user_id}",
                {"lang": "en_US", "Authorization": f"Bearer {token}"})["data"]

    set_topic = f"/app/{user_id}/{sn}/thing/property/set"
    reply_topic = f"/app/{user_id}/{sn}/thing/property/set_reply"

    def on_message(c, u, m):
        if m.topic.endswith("set_reply"):
            print(f"  ACK ({len(m.payload)}B): {m.payload.hex()}")

    def on_connect(c, u, f, rc, *a):
        c.subscribe(reply_topic, qos=1)

    import uuid
    cid = f"ANDROID_{uuid.uuid4().hex.upper()}_{user_id}"
    try:
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=cid, protocol=mqtt.MQTTv311)
    except (AttributeError, TypeError):
        c = mqtt.Client(client_id=cid, protocol=mqtt.MQTTv311)
    c.username_pw_set(cert["certificateAccount"], password=cert["certificatePassword"])
    c.tls_set_context(ssl.create_default_context())
    c.on_connect = on_connect; c.on_message = on_message
    c.connect(cert["url"], int(cert["port"]), keepalive=60)
    c.loop_start()
    time.sleep(2)

    cid_num = control_id_for(circuit)
    print(f"Circuit {circuit} -> control-id {cid_num} on {sn}")
    seq = int(time.time()) & 0xFFFF
    print(f"Turning circuit {circuit} OFF ...")
    c.publish(set_topic, build_circuit_command(sn, circuit, False, seq), qos=1)
    time.sleep(4)
    print(f"Turning circuit {circuit} ON ...")
    c.publish(set_topic, build_circuit_command(sn, circuit, True, seq + 1), qos=1)
    time.sleep(3)
    c.loop_stop(); c.disconnect()
    print("Done. Did the RIGHT circuit toggle off then on? Tell me yes/no + which circuit reacted.")


if __name__ == "__main__":
    main()
