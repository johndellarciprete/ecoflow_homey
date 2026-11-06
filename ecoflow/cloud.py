"""EcoFlow private (app-account) cloud client — login, certification, MQTT.

The official developer API blocks these devices (error 1006), so we use the same
private cloud the EcoFlow phone app uses: email + password -> token -> MQTT
credentials -> live protobuf pushes (decoded by `proto.py`). Credentials are
stored per device (like the Rheem app); the token is re-fetched on demand.

All HTTPS goes through `new_session()` (certifi) — Homey's runtime ships no CA
store. paho is synchronous, so MQTT runs in its own thread and marshals decoded
params back onto the asyncio loop.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import uuid
from typing import Any, Callable

import aiohttp

from .api import make_ssl_context, new_session  # shared TLS helpers
from .proto import decode_message

_LOGGER = logging.getLogger(__name__)

API_DOMAINS = {"us": "api.ecoflow.com", "global": "api.ecoflow.com", "eu": "api-e.ecoflow.com"}
DEFAULT_REGION = "us"
MQTT_KEEPALIVE = 60


class EcoFlowCloud:
    """Private app-account REST auth (login + MQTT certification)."""

    def __init__(
        self,
        email: str,
        password: str,
        session: aiohttp.ClientSession,
        region: str = DEFAULT_REGION,
    ) -> None:
        self._email = email
        self._password = password
        self._session = session
        self._domain = API_DOMAINS.get(region, API_DOMAINS[DEFAULT_REGION])
        self.region = region
        self.token: str | None = None
        self.user_id: str | None = None
        self.user_name: str | None = None

    @property
    def email(self) -> str:
        return self._email

    async def login(self) -> None:
        body = {
            "email": self._email,
            "password": base64.b64encode(self._password.encode()).decode(),
            "scene": "IOT_APP",
            "userType": "ECOFLOW",
        }
        headers = {"lang": "en_US", "Content-Type": "application/json"}
        try:
            async with self._session.post(
                f"https://{self._domain}/auth/login", json=body, headers=headers
            ) as r:
                data = await r.json(content_type=None)
        except aiohttp.ClientError as e:
            raise EcoFlowError(f"login network error: {e}") from e
        if str(data.get("code")) not in ("0", "200"):
            raise EcoFlowAuthError(
                f"login failed: code={data.get('code')} message={data.get('message')!r}"
            )
        d = data.get("data") or {}
        user = d.get("user") or {}
        self.token = d.get("token")
        self.user_id = user.get("userId")
        self.user_name = user.get("name")
        if not self.token or not self.user_id:
            raise EcoFlowAuthError("login ok but no token/userId returned")

    async def ensure_login(self) -> None:
        if not self.token or not self.user_id:
            await self.login()

    async def get_certification(self) -> dict:
        await self.ensure_login()
        headers = {"lang": "en_US", "Authorization": f"Bearer {self.token}"}
        url = f"https://{self._domain}/iot-auth/app/certification?userId={self.user_id}"
        try:
            async with self._session.get(url, headers=headers) as r:
                data = await r.json(content_type=None)
        except aiohttp.ClientError as e:
            raise EcoFlowError(f"certification network error: {e}") from e
        if str(data.get("code")) not in ("0", "200"):
            # token likely expired — re-login once
            self.token = None
            await self.login()
            headers["Authorization"] = f"Bearer {self.token}"
            async with self._session.get(url, headers=headers) as r:
                data = await r.json(content_type=None)
            if str(data.get("code")) not in ("0", "200"):
                raise EcoFlowError(f"certification failed: {data.get('message')!r}")
        return data.get("data") or {}

    def to_dict(self) -> dict:
        return {"email": self._email, "password": self._password, "region": self.region}


class EcoFlowMqtt:
    """Persistent MQTT/TLS subscriber that decodes protobuf pushes.

    Subscribes under the user's own topic tree (`/app/{userId}/#`), decodes each
    payload with `proto.decode_message`, and calls `on_params(module_sn, params)`
    on the asyncio loop. Decoded params carry `module_sn`/`device_sn` so a device
    can filter to its own SN.
    """

    def __init__(
        self,
        cert: dict,
        user_id: str,
        loop: asyncio.AbstractEventLoop,
        on_params: Callable[[str, dict], None],
        sns: list[str],
        log: Callable[[str], None] | None = None,
        decoder: Callable[[bytes], dict] | None = None,
    ) -> None:
        self._cert = cert
        self._user_id = user_id
        self._loop = loop
        self._on_params = on_params
        self._sns = [s for s in sns if s]
        self._log = log or (lambda _m: None)
        self._decode = decoder or decode_message
        self._client = None
        self.connected = False
        self.msg_count = 0
        self.subscribe_denied = False  # broker rejected a topic (bad serial)

    def start(self) -> None:
        import paho.mqtt.client as mqtt

        acct = str(self._cert.get("certificateAccount") or "")
        pw = str(self._cert.get("certificatePassword") or "")
        host = str(self._cert.get("url") or "mqtt.ecoflow.com")
        port = int(self._cert.get("port") or 8883)
        cid = f"ANDROID_{uuid.uuid4().hex.upper()}_{self._user_id}"
        try:
            client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION1, client_id=cid, protocol=mqtt.MQTTv311
            )
        except (AttributeError, TypeError):  # paho 1.x
            client = mqtt.Client(client_id=cid, protocol=mqtt.MQTTv311)
        client.username_pw_set(acct, password=pw)
        client.tls_set_context(make_ssl_context())
        client.on_connect = self._on_connect
        client.on_subscribe = self._on_subscribe
        client.on_message = self._on_message
        self._client = client
        try:
            client.connect(host, port, keepalive=MQTT_KEEPALIVE)
        except Exception as e:  # noqa: BLE001
            self._log(f"MQTT connect error to {host}:{port}: {e}")
            raise
        client.loop_start()
        self._log(f"MQTT connecting to {host}:{port} as {acct}")

    def _on_connect(self, client, _u, _flags, rc, *_a):
        if rc not in (0, None):
            self.connected = False
            self._log(f"MQTT connect REFUSED rc={rc}")
            return
        self.connected = True
        # Device TELEMETRY is on /app/device/property/{sn}; the reply to our
        # latestQuotas get (which carries the full state incl. circuit names) is
        # on /app/{userId}/{sn}/thing/property/get_reply. The broker's ACL grants
        # only EXACT per-serial topics, so subscribe both, per serial.
        for sn in self._sns:
            client.subscribe(f"/app/device/property/{sn}", qos=1)
            client.subscribe(f"/app/{self._user_id}/{sn}/thing/property/get_reply", qos=1)
        self._log(f"MQTT connected; subscribed telemetry+get_reply for: {self._sns}")

    def _on_subscribe(self, _client, _u, _mid, granted_qos, *_a):
        # granted_qos entries of 0x80 (128) mean the broker DENIED that subscribe
        # — for these per-serial topics that means the serial is wrong/unknown.
        vals = [getattr(q, "value", q) for q in granted_qos] if granted_qos else []
        if any(v == 128 for v in vals):
            self.subscribe_denied = True
        self._log(f"MQTT suback granted_qos={vals}")

    def _on_message(self, _client, _u, msg):
        self.msg_count += 1
        topic_sn = self._sn_from_topic(msg.topic)
        try:
            params = self._decode(msg.payload)
        except Exception as e:  # noqa: BLE001
            self._log(f"decode failed on {msg.topic}: {e}")
            return
        if self.msg_count <= 3 or msg.topic.endswith("get_reply"):
            self._log(f"msg on {msg.topic} ({len(msg.payload)}B) -> {len(params)} params")
        if not params:
            return
        # Route by the serial in the TOPIC (robust — the payload's own sn field
        # isn't always populated, e.g. on get_reply frames).
        sn = topic_sn or params.get("module_sn") or params.get("device_sn") or ""
        self._loop.call_soon_threadsafe(self._on_params, sn, params)

    @staticmethod
    def _sn_from_topic(topic: str) -> str:
        parts = topic.split("/")
        # /app/device/property/{sn}
        if len(parts) >= 5 and parts[2] == "device" and parts[3] == "property":
            return parts[4]
        # /app/{userId}/{sn}/thing/property/...
        if "thing" in parts:
            i = parts.index("thing")
            if i >= 1:
                return parts[i - 1]
        return ""

    def send_get(self, sn: str) -> None:
        """Publish a JSON `latestQuotas` request to prompt a full push."""
        if not self._client or not sn:
            return
        topic = f"/app/{self._user_id}/{sn}/thing/property/get"
        payload = json.dumps(
            {"version": "1.1", "moduleType": 0, "operateType": "latestQuotas", "params": {}}
        )
        try:
            self._client.publish(topic, payload=payload, qos=1)
            self._log(f"sent latestQuotas get -> {topic}")
        except Exception as e:  # noqa: BLE001
            self._log(f"send_get error: {e}")

    def publish_set(self, sn: str, payload: bytes) -> bool:
        """Publish a raw control payload to a device's `set` topic."""
        if not self._client or not sn:
            return False
        topic = f"/app/{self._user_id}/{sn}/thing/property/set"
        try:
            self._client.publish(topic, payload=payload, qos=1)
            self._log(f"published set command ({len(payload)}B) -> {topic}")
            return True
        except Exception as e:  # noqa: BLE001
            self._log(f"publish_set error: {e}")
            return False

    def stop(self) -> None:
        if self._client is not None:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            self._client = None


# ── Exceptions (reuse the api hierarchy) ─────────────────────────────────────
from .api import EcoFlowAPIError, EcoFlowAuthError, EcoFlowError  # noqa: E402,F401
