"""Constants for the EcoFlow developer-API client.

EcoFlow's IoT Open Platform (https://developer.ecoflow.com) is the OFFICIAL,
sanctioned API — unlike the reverse-engineered logins in the Rheem/ecobee apps.
Auth is an AccessKey + SecretKey the user creates on the dev portal; requests are
HMAC-SHA256 signed. Keys are scoped to that account's own devices, so each user
enters their own keys (there are no shared baked-in credentials here).
"""

from __future__ import annotations

# ── Region hosts ─────────────────────────────────────────────────────────────
# The account's region decides which host answers. US/global share one host.
HOSTS = {
    "us": "https://api.ecoflow.com",
    "global": "https://api.ecoflow.com",
    "eu": "https://api-e.ecoflow.com",
}
DEFAULT_REGION = "us"

# ── REST endpoints (all under the signed /iot-open/sign prefix) ──────────────
PATH_DEVICE_LIST = "/iot-open/sign/device/list"
PATH_QUOTA_ALL = "/iot-open/sign/device/quota/all"   # GET ?sn=
PATH_QUOTA = "/iot-open/sign/device/quota"            # POST (get specific) / PUT (set)
PATH_CERTIFICATION = "/iot-open/sign/certification"   # GET -> MQTT creds

# EcoFlow wraps every REST response as {code, message, data}. These codes = OK.
OK_CODES = {"0", "200"}

# ── MQTT ─────────────────────────────────────────────────────────────────────
# Broker host/port/username/password come from the certification endpoint at
# runtime; only the topic templates are fixed. certificateAccount is the MQTT
# username returned by certification.
MQTT_KEEPALIVE = 60
TOPIC_QUOTA = "/open/{account}/{sn}/quota"        # live field push (subscribe)
TOPIC_STATUS = "/open/{account}/{sn}/status"      # online/offline (subscribe)
TOPIC_SET = "/open/{account}/{sn}/set"            # command (publish)
TOPIC_SET_REPLY = "/open/{account}/{sn}/set_reply"  # command ack (subscribe)

# ── Polling ──────────────────────────────────────────────────────────────────
# quota/all HTTP poll is the fallback when MQTT is stale/unavailable.
POLL_INTERVAL_S = 60
MQTT_STALE_AFTER_S = 90
