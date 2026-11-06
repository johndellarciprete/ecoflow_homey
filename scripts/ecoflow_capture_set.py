#!/usr/bin/env python3
"""
Capture the EcoFlow panel's circuit ON/OFF control command.

The Smart Home Panel supports remote circuit control, but the command format
isn't public. This listens on the panel's `set` topic while YOU toggle a circuit
in the EcoFlow phone app — the app publishes the control protobuf to that topic,
and we capture it so we can replay it from Homey.

Password handling and login are identical to ecoflow_app_probe.py: hidden prompt,
never printed, never sent anywhere but EcoFlow's login endpoint.

USAGE (PowerShell):
    python ecoflow_capture_set.py johndellarciprete@gmail.com

Then, while it's listening, in the EcoFlow app:
    1. Turn a circuit OFF, wait 2s.
    2. Turn the SAME circuit back ON, wait 2s.
    3. Note which circuit number you toggled.
It writes ecoflow_set_<n>.bin / .hex.txt for each captured control message.
Send me those files + which circuit number you toggled.
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
import uuid

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
PANEL_SN = "HR63ZA1AVJ1E0369"
API = "api.ecoflow.com"


def _post(url, body, headers):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _get(url, headers):
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def main():
    args = sys.argv[1:]
    seconds = 90
    if "--seconds" in args:
        i = args.index("--seconds"); seconds = int(args[i + 1]); del args[i:i + 2]
    sn = PANEL_SN
    if "--sn" in args:
        i = args.index("--sn"); sn = args[i + 1].strip().upper(); del args[i:i + 2]
    email = args[0] if args else os.environ.get("ECOFLOW_EMAIL") or input("EcoFlow email: ").strip()
    password = os.environ.get("ECOFLOW_PASSWORD") or getpass.getpass("EcoFlow password (hidden): ")

    import paho.mqtt.client as mqtt

    print("Logging in ...")
    resp = _post(f"https://{API}/auth/login",
                 {"email": email, "password": base64.b64encode(password.encode()).decode(),
                  "scene": "IOT_APP", "userType": "ECOFLOW"},
                 {"lang": "en_US", "Content-Type": "application/json"})
    if str(resp.get("code")) not in ("0", "200"):
        raise SystemExit(f"login failed: {resp.get('message')!r}")
    d = resp["data"]; token = d["token"]; user_id = d["user"]["userId"]
    print(f"   OK userId={user_id}")
    cert = _get(f"https://{API}/iot-auth/app/certification?userId={user_id}",
                {"lang": "en_US", "Authorization": f"Bearer {token}"})["data"]
    acct = cert["certificateAccount"]; pw = cert["certificatePassword"]

    topics = [
        f"/app/{user_id}/{sn}/thing/property/set",
        f"/app/{user_id}/{sn}/thing/property/set_reply",
        f"/app/{user_id}/{sn}/thing/property/get_reply",
    ]
    n = [0]

    def on_connect(c, u, f, rc, *a):
        print("MQTT connect rc =", rc)
        for t in topics:
            c.subscribe(t, qos=1); print("  sub", t)

    def on_subscribe(c, u, mid, granted, *a):
        vals = [getattr(q, "value", q) for q in granted] if granted else []
        print("  suback granted_qos =", vals, "(128 = denied)")

    def on_message(c, u, m):
        n[0] += 1
        raw = m.payload
        base = f"ecoflow_set_{n[0]}"
        try:
            obj = json.loads(raw.decode())
            with open(os.path.join(OUT_DIR, base + ".json"), "w") as fh:
                json.dump({"topic": m.topic, "payload": obj}, fh, indent=2)
            print(f"  CAPTURED [JSON] {m.topic} -> {base}.json")
        except (ValueError, UnicodeDecodeError):
            with open(os.path.join(OUT_DIR, base + ".bin"), "wb") as fb:
                fb.write(raw)
            with open(os.path.join(OUT_DIR, base + ".hex.txt"), "w") as fx:
                fx.write(raw.hex())
            print(f"  CAPTURED [BINARY] {m.topic} ({len(raw)}B) -> {base}.hex.txt")

    cid = f"ANDROID_{uuid.uuid4().hex.upper()}_{user_id}"
    try:
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=cid, protocol=mqtt.MQTTv311)
    except (AttributeError, TypeError):
        c = mqtt.Client(client_id=cid, protocol=mqtt.MQTTv311)
    c.username_pw_set(acct, password=pw)
    c.tls_set_context(ssl.create_default_context())
    c.on_connect = on_connect; c.on_subscribe = on_subscribe; c.on_message = on_message
    c.connect(cert["url"], int(cert["port"]), keepalive=60)
    c.loop_start()
    print("=" * 64)
    print(f"LISTENING {seconds}s. Now, in the EcoFlow app: turn a circuit OFF,")
    print("wait 2s, turn it back ON. Note the circuit number.")
    print("=" * 64)
    time.sleep(seconds)
    c.loop_stop(); c.disconnect()
    print(f"Done. Captured {n[0]} message(s). Send me the ecoflow_set_* files.")


if __name__ == "__main__":
    main()
