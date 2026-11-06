#!/usr/bin/env python3
"""
EcoFlow APP-LOGIN probe (private cloud API) — captures live device payloads.

The official developer API blocks these devices (error 1006). This probe uses the
SAME private cloud the EcoFlow phone app uses: your app email + password -> a
token -> private MQTT credentials -> live device pushes. It then dumps whatever
your Delta Pro Ultra X and Smart Home Panel 3 actually send, so we can map it.

Your PASSWORD never leaves this machine and is never printed: it's read from a
hidden prompt (or the ECOFLOW_PASSWORD env var) and sent only to EcoFlow's login
endpoint over HTTPS. Nothing is sent to Claude — you send me the dump FILES.

Requires: paho-mqtt (already present in your Homey app env). If missing:
    pip install paho-mqtt

USAGE (PowerShell):
    python ecoflow_app_probe.py you@example.com
    python ecoflow_app_probe.py you@example.com --seconds 90
    (or set ECOFLOW_EMAIL / ECOFLOW_PASSWORD env vars and run with no args)

Output (written next to this script):
    ecoflow_app_login.json            - user id + which api domain worked (no token/password)
    ecoflow_app_cert.json             - MQTT broker coords (password redacted)
    ecoflow_app_msg_<sn>_<n>.json     - a JSON payload the device pushed
    ecoflow_app_msg_<sn>_<n>.bin      - a RAW (likely protobuf) payload + a .hex.txt view
    ecoflow_app_topics.json           - every topic that produced traffic
Send me the ecoflow_app_* files (they contain device data, not credentials).
"""

from __future__ import annotations

import base64
import getpass
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
import uuid

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
# The private cloud has answered on both of these historically; we try in order.
API_DOMAINS = ["api.ecoflow.com", "api-a.ecoflow.com"]


def _post_json(url: str, body: dict, headers: dict) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _get_json(url: str, headers: dict) -> dict:
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def login(email: str, password: str):
    """Return (api_domain, token, user_id, user_name). Tries both domains."""
    body = {
        "email": email,
        "password": base64.b64encode(password.encode()).decode(),
        "scene": "IOT_APP",
        "userType": "ECOFLOW",
    }
    headers = {"lang": "en_US", "Content-Type": "application/json"}
    last_err = None
    for domain in API_DOMAINS:
        try:
            resp = _post_json(f"https://{domain}/auth/login", body, headers)
        except urllib.error.HTTPError as e:
            last_err = f"{domain}: HTTP {e.code} {e.read().decode('utf-8','replace')[:200]}"
            continue
        except urllib.error.URLError as e:
            last_err = f"{domain}: {e}"
            continue
        if str(resp.get("code")) not in ("0", "200"):
            last_err = f"{domain}: code={resp.get('code')} message={resp.get('message')!r}"
            continue
        data = resp.get("data") or {}
        user = data.get("user") or {}
        token = data.get("token")
        user_id = user.get("userId")
        if token and user_id:
            return domain, token, user_id, user.get("name")
        last_err = f"{domain}: login ok but missing token/userId"
    raise SystemExit(f"Login failed. {last_err}")


def certification(domain: str, token: str, user_id: str) -> dict:
    headers = {"lang": "en_US", "Authorization": f"Bearer {token}"}
    url = f"https://{domain}/iot-auth/app/certification?userId={user_id}"
    resp = _get_json(url, headers)
    if str(resp.get("code")) not in ("0", "200"):
        raise SystemExit(f"certification failed: code={resp.get('code')} "
                         f"message={resp.get('message')!r}")
    return resp.get("data") or {}


def _write(name: str, obj) -> None:
    with open(os.path.join(OUT_DIR, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False, sort_keys=True)


def main():
    args = [a for a in sys.argv[1:]]
    seconds = 60
    if "--seconds" in args:
        i = args.index("--seconds")
        seconds = int(args[i + 1])
        del args[i:i + 2]
    email = args[0] if args else os.environ.get("ECOFLOW_EMAIL") or input("EcoFlow email: ").strip()
    password = os.environ.get("ECOFLOW_PASSWORD") or getpass.getpass("EcoFlow password (hidden): ")
    if not email or not password:
        raise SystemExit("Email and password are required.")

    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        raise SystemExit("paho-mqtt not installed. Run:  pip install paho-mqtt")

    print("Logging in ...")
    domain, token, user_id, name = login(email, password)
    print(f"   OK via {domain}  user={name!r}  userId={user_id}")
    _write("ecoflow_app_login.json", {"api_domain": domain, "user_id": user_id, "user_name": name})

    print("Fetching MQTT certification ...")
    cert = certification(domain, token, user_id)
    host = cert.get("url"); port = int(cert.get("port") or 8883)
    acct = cert.get("certificateAccount"); pw = cert.get("certificatePassword")
    print(f"   broker {host}:{port}  protocol={cert.get('protocol')}  account={acct}")
    _write("ecoflow_app_cert.json",
           {k: ("<redacted>" if k == "certificatePassword" else v) for k, v in cert.items()})

    # SNs from the developer-API device list (same account); adjust if needed.
    # HR63… = Smart Home Panel 3, P101… = Delta Pro Ultra X, MT11… = PowerInsight.
    sns = ["HR63ZA1AVJ1E0369", "P101ZA1A9J470312", "MT11ZA1BKHCA0413"]
    counters: dict[str, int] = {}
    topics: dict[str, int] = {}

    def on_connect(c, u, f, rc, *a):
        print("MQTT connect rc =", rc)
        # The broker's ACL only grants EXACT per-serial topics (wildcards are
        # denied), so subscribe one telemetry topic per serial...
        for sn in sns:
            t = f"/app/device/property/{sn}"
            c.subscribe(t, qos=1); print("  sub", t)
            # Also capture the reply to our get (circuit names may live here).
            rt = f"/app/{user_id}/{sn}/thing/property/get_reply"
            c.subscribe(rt, qos=1); print("  sub", rt)
        # ...and request a full push so idle devices report right away.
        get = json.dumps({"version": "1.1", "moduleType": 0,
                          "operateType": "latestQuotas", "params": {}})
        for sn in sns:
            gt = f"/app/{user_id}/{sn}/thing/property/get"
            c.publish(gt, get, qos=1); print("  get", gt)

    def on_subscribe(c, u, mid, granted_qos, *a):
        vals = [getattr(q, "value", q) for q in granted_qos] if granted_qos else []
        print("  suback granted_qos =", vals, "(128 = denied)")

    def on_message(c, u, m):
        topics[m.topic] = topics.get(m.topic, 0) + 1
        sn = next((s for s in sns if s in m.topic), "unknown")
        tag = "reply" if m.topic.endswith("get_reply") else "prop"
        key = f"{sn}_{tag}"
        n = counters.get(key, 0) + 1
        counters[key] = n
        raw = m.payload
        try:
            obj = json.loads(raw.decode("utf-8"))
            kind = "JSON"
            if n <= 15:
                _write(f"ecoflow_app_msg_{sn}_{tag}_{n}.json", {"topic": m.topic, "payload": obj})
        except (ValueError, UnicodeDecodeError):
            kind = "BINARY(protobuf?)"
            if n <= 15:
                with open(os.path.join(OUT_DIR, f"ecoflow_app_msg_{sn}_{tag}_{n}.bin"), "wb") as fb:
                    fb.write(raw)
                with open(os.path.join(OUT_DIR, f"ecoflow_app_msg_{sn}_{tag}_{n}.hex.txt"), "w") as fh:
                    fh.write(raw.hex())
        print(f"  MSG [{kind}] {m.topic}  ({len(raw)} bytes)")

    cid = f"ANDROID_{uuid.uuid4().hex.upper()}_{user_id}"
    try:
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=cid, protocol=mqtt.MQTTv311)
    except (AttributeError, TypeError):
        c = mqtt.Client(client_id=cid, protocol=mqtt.MQTTv311)
    c.username_pw_set(acct, password=pw)
    c.tls_set_context(ssl.create_default_context())
    c.on_connect = on_connect
    c.on_subscribe = on_subscribe
    c.on_message = on_message
    c.connect(host, port, keepalive=60)
    c.loop_start()
    print(f"Listening {seconds}s (turn something on/off in the EcoFlow app to force a push) ...")
    time.sleep(seconds)
    c.loop_stop(); c.disconnect()

    _write("ecoflow_app_topics.json", topics)
    total = sum(counters.values())
    print("=" * 60)
    print(f"Captured {total} message(s) across {len(topics)} topic(s).")
    if total == 0:
        print("No pushes. Try: keep the EcoFlow app OPEN on your phone while this runs, "
              "and toggle a load — the cloud often only streams when a client is active.")
    else:
        print(f"Wrote dumps to {OUT_DIR}. Send me the ecoflow_app_* files.")


if __name__ == "__main__":
    main()
