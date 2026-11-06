#!/usr/bin/env python3
"""
EcoFlow developer-API probe — dumps your devices' live quota fields.

This is a STANDALONE diagnostic (pure standard library — no pip installs). Run it
once you have an AccessKey / SecretKey from https://developer.ecoflow.com. It:

  1. Lists every device bound to your EcoFlow account (device/list).
  2. Pulls the full live "quota/all" field map for each device.
  3. Fetches the MQTT certification (so we know the broker host/port/creds later).
  4. Writes everything to JSON next to this script.

The quota dumps for your Smart Home Panel 3 and Delta Pro Ultra X are the
blueprint we reverse-map into Homey capabilities (EcoFlow hasn't published a
quota schema for these new models yet, so we derive it from your real devices).

USAGE (PowerShell), any ONE of:
    python ecoflow_probe.py <ACCESS_KEY> <SECRET_KEY>
    python ecoflow_probe.py <ACCESS_KEY> <SECRET_KEY> --region eu
    $env:ECOFLOW_ACCESS_KEY="ak..."; $env:ECOFLOW_SECRET_KEY="sk..."; python ecoflow_probe.py
    python ecoflow_probe.py            # prompts for both keys

Region: default is the global/US host (api.ecoflow.com). Pass --region eu if your
EcoFlow account is registered in Europe (api-e.ecoflow.com).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

HOSTS = {
    "us": "https://api.ecoflow.com",
    "global": "https://api.ecoflow.com",
    "eu": "https://api-e.ecoflow.com",
}

OUT_DIR = os.path.dirname(os.path.abspath(__file__))


# ── HMAC signing ────────────────────────────────────────────────────────────
def _flatten(obj, prefix=""):
    """Flatten nested params the way EcoFlow's signature spec requires.

    {"a": {"b": 1}}      -> {"a.b": "1"}
    {"ids": [1, 2]}      -> {"ids[0]": "1", "ids[1]": "2"}
    Booleans render lowercase; everything else as a plain string.
    """
    items = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            items.update(_flatten(v, key))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            items.update(_flatten(v, f"{prefix}[{i}]"))
    else:
        if isinstance(obj, bool):
            val = "true" if obj else "false"
        else:
            val = str(obj)
        items[prefix] = val
    return items


def _sign(access_key: str, secret_key: str, params: dict | None):
    """Return the headers dict (accessKey/nonce/timestamp/sign) for a request."""
    nonce = str(random.randint(100000, 999999))
    timestamp = str(int(time.time() * 1000))
    parts = []
    if params:
        flat = _flatten(params)
        parts.extend(f"{k}={flat[k]}" for k in sorted(flat.keys()))
    parts.append(f"accessKey={access_key}")
    parts.append(f"nonce={nonce}")
    parts.append(f"timestamp={timestamp}")
    sign_str = "&".join(parts)
    sign = hmac.new(
        secret_key.encode("utf-8"), sign_str.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return {
        "accessKey": access_key,
        "nonce": nonce,
        "timestamp": timestamp,
        "sign": sign,
        "Content-Type": "application/json;charset=UTF-8",
    }


# ── HTTP (GET only; the probe never writes to a device) ─────────────────────
def _get(base: str, path: str, access_key: str, secret_key: str, query: dict | None = None):
    url = base + path
    if query:
        qs = "&".join(f"{k}={query[k]}" for k in sorted(query))
        url = f"{url}?{qs}"
    # NOTE: EcoFlow's GET signature does NOT include query params — signing them
    # yields code 8521 "signature is wrong". Only accessKey/nonce/timestamp are
    # signed (proven empirically against the live API 2026-08-27).
    headers = _sign(access_key, secret_key, None)
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise SystemExit(f"HTTP {e.code} on {path}:\n{body}")
    except urllib.error.URLError as e:
        raise SystemExit(f"Network error on {path}: {e}")
    data = json.loads(body)
    # EcoFlow wraps every response as {code, message, data}. code "0" == success.
    if str(data.get("code")) not in ("0", "200"):
        raise SystemExit(f"API error on {path}: code={data.get('code')} "
                         f"message={data.get('message')!r}\n{json.dumps(data, indent=2)}")
    return data


def _write(name: str, obj) -> str:
    path = os.path.join(OUT_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False, sort_keys=True)
    return path


# ── main ────────────────────────────────────────────────────────────────────
def _read_keys(argv):
    region = "us"
    positional = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--region" and i + 1 < len(argv):
            region = argv[i + 1].lower()
            i += 2
            continue
        positional.append(a)
        i += 1
    access = secret = None
    if len(positional) >= 2:
        access, secret = positional[0], positional[1]
    else:
        access = os.environ.get("ECOFLOW_ACCESS_KEY")
        secret = os.environ.get("ECOFLOW_SECRET_KEY")
    if not access:
        access = input("EcoFlow AccessKey: ").strip()
    if not secret:
        secret = input("EcoFlow SecretKey: ").strip()
    if not access or not secret:
        raise SystemExit("Both AccessKey and SecretKey are required.")
    return access, secret, region


def main():
    access, secret, region = _read_keys(sys.argv[1:])
    base = HOSTS.get(region, HOSTS["us"])
    print(f"Region: {region}  ->  {base}")
    print("=" * 70)

    # 1. device list
    print("Fetching device list ...")
    dl = _get(base, "/iot-open/sign/device/list", access, secret)
    devices = dl.get("data") or []
    _write("ecoflow_devices.json", dl)
    if not devices:
        print("No devices returned. Is anything bound to this EcoFlow account?")
        return
    print(f"Found {len(devices)} device(s):")
    for d in devices:
        print(f"   - SN {d.get('sn')!r}  online={d.get('online')}  "
              f"name={d.get('productName') or d.get('deviceName')!r}")
    print("=" * 70)

    # 2. quota/all per device
    summary = []
    for d in devices:
        sn = d.get("sn")
        if not sn:
            continue
        print(f"Fetching quota/all for {sn} ...")
        try:
            q = _get(base, "/iot-open/sign/device/quota/all", access, secret, {"sn": sn})
        except SystemExit as e:
            print(f"   ! {e}")
            continue
        fields = q.get("data") or {}
        safe_sn = "".join(c if c.isalnum() else "_" for c in str(sn))
        path = _write(f"ecoflow_quota_{safe_sn}.json", q)
        keys = sorted(fields.keys()) if isinstance(fields, dict) else []
        print(f"   {len(keys)} fields -> {os.path.basename(path)}")
        summary.append({"sn": sn, "productName": d.get("productName"),
                        "field_count": len(keys), "fields": keys})

    # 3. MQTT certification (host/port/creds for the live push channel)
    print("=" * 70)
    print("Fetching MQTT certification ...")
    try:
        cert = _get(base, "/iot-open/sign/certification", access, secret)
        cdata = cert.get("data") or {}
        # Don't print the certificatePassword; just confirm the broker coords.
        print(f"   broker: {cdata.get('url')}:{cdata.get('port')} "
              f"protocol={cdata.get('protocol')} account={cdata.get('certificateAccount')}")
        _write("ecoflow_certification.json", cert)
    except SystemExit as e:
        print(f"   ! certification failed: {e}")

    _write("ecoflow_probe_summary.json",
           {"generated": datetime.now().isoformat(timespec="seconds"),
            "region": region, "devices": summary})
    print("=" * 70)
    print(f"Done. JSON written to: {OUT_DIR}")
    print("Send me ecoflow_devices.json + the ecoflow_quota_*.json files and I'll "
          "map the fields to Homey capabilities.")


if __name__ == "__main__":
    main()
