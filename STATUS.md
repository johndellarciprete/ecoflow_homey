# EcoFlow Homey app — build status

Homey Python-SDK app for the **EcoFlow Smart Home Panel 3** + **Delta Pro Ultra X**
(the 2026 gen the official EcoFlow Homey app doesn't cover). Built on EcoFlow's
**official developer API** (developer.ecoflow.com) — AccessKey/SecretKey + HMAC,
MQTT live push with HTTP poll fallback. This is the sanctioned API, so no
reverse-engineering / trademark risk like the Rheem/ecobee apps.

## What's done (works without credentials)
- `scripts/ecoflow_probe.py` — **pure-stdlib** diagnostic. Run it the moment your
  keys arrive; it dumps `device/list` + `quota/all` + MQTT certification to JSON.
  Signing verified byte-for-byte against the app client.
- `ecoflow/api.py` — async client: HMAC signing (`sign_headers`), REST
  (device list / quota-all / set / certification), and `EcoFlowMqtt` live
  subscriber. `ecoflow/const.py` — hosts, endpoints, topics.
- `ecoflow/pairing.py` — shared AccessKey/SecretKey pair flow (validates keys by
  listing devices, filters per driver). Keys are the USER's own → stored per
  device; nothing baked into the package.
- Drivers `deltaproultrax` + `smartpanel3` — driver.py (pairing), device.py
  (subclass of `ecoflow/device_base.py`), and the credential pair view.
- `ecoflow/device_base.py` — connects, starts MQTT + poll, and **logs the full
  live quota field map** on first fetch (that log is the mapping blueprint). It
  conservatively sets `measure_battery` from an obvious SoC field and guesses
  nothing else.
- Manifest (`app.json`), distinct placeholder icons (SVG) + images (all required
  sizes). Manifest passes `homey app validate` pre-processing.

## 2026-08-27: developer API is BLOCKED for these devices — pivoting
Tested with live keys (AccessKey 8EGirx7N...). `device/list` + `certification`
work; account has `P101ZA1A9J470312` (online, likely DPU X) + `MT11ZA1BKHCA0413`
"PowerInsight" (offline, likely the Panel 3 / meter). But `quota/all` returns
**1006 "current device is not allowed to get device info"** for BOTH, and a 110 s
wildcard MQTT subscribe got **zero** messages. This is EcoFlow's mid-2025
official-API crackdown, not our bug. Signing fix learned: EcoFlow does NOT sign
GET query params (fixed in probe + api.py).

**Decision: pivot to the app-login private API** (email/password → private MQTT),
like the other reverse-engineered apps + the official EcoFlow Homey app. The
dev-API code (`ecoflow/api.py`, `ecoflow_probe.py`) is kept in case EcoFlow
whitelists later.

### Phase 1 BUILT (Delta Pro Ultra X monitoring) — validated offline
Payloads are **protobuf** = the **Delta Pro 3 dialect** (schema vendored in `reference/`).
Decoded a live capture and got `cms_batt_soc = 92.0`, matching the app's 92%. Built:
- `ecoflow/proto.py` — dependency-free protobuf wire decoder (nothing to cross-compile),
  driven by `ecoflow/proto_fields.py` (generated from the DP3 .proto by
  `scripts/gen_proto_fields.py`; 281 fields). Handles the envelope, cmdId 21/22 partial
  updates, and the DPU-X per-pack extra (field 786).
- `ecoflow/cloud.py` — private app-cloud: login (email/pw) → certification → MQTT
  subscribe + decode. `ecoflow/mapping.py` — params → Homey caps (pure, unit-testable).
- `ecoflow/device_base.py` — MQTT-driven runtime (no HTTP poll; watchdog reconnect).
- Pairing rewired to email/password + MQTT discovery (`ecoflow/pairing.py` + both pair
  views). Delta driver/device map SoC, in/out/net power, charge state, time remaining,
  L1/L2 voltage. Manifest caps defined (class "other"). All compile; manifest valid JSON;
  decode→map verified end-to-end.

### Still to do
- **Install & live-test:** `homey app install` (needs Docker running for the dep compile;
  flaky uv_cache → `rm -rf python_packages` + retry), then pair with EcoFlow email/password
  and confirm live values on Homey.
- **Panel 3 (MT11):** capture after the Fri 2026-08-28 install → map schema → panel driver
  (per-circuit monitoring).
- **Phase 3 control:** capture "set" MQTT messages (toggle in the app) → add actuation.
  Reference DPU X is read-only.
- Real artwork; extra sensors (per-pack SoC/temp, solar, temps) as wanted.

## Notes
- `homey app validate` / `install` need **Docker running** (Python venv compile).
  Known flaky uv_cache "Permission denied" → `rm -rf python_packages` + retry.
- `homey app publish`/`install` are interactive → run by John, not the agent.
- Repo can be public here (no shared secret shipped — keys are user-entered).
