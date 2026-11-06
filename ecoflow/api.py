"""
EcoFlow developer-API async client (official IoT Open Platform).

Auth:   AccessKey + SecretKey (user-supplied, from developer.ecoflow.com). Every
        request is HMAC-SHA256 signed — see `sign_headers`. There is no token to
        refresh; the keys sign each call directly.
Read:   GET device/list enumerates the account's devices; GET device/quota/all
        returns a device's full live field map. HTTP polling is the FALLBACK.
        The PRIMARY read channel is MQTT (`EcoFlowMqtt`) — the cloud pushes quota
        deltas the instant they change.
Write:  PUT device/quota sends a command (body params are signed like any other).

As in the Rheem app, all HTTPS goes through `new_session()` so certifi's CA
bundle is used — Homey's Python runtime ships no system CA store.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import random
import ssl
import threading
import time
from typing import Any, Callable

import aiohttp

from .const import (
    DEFAULT_REGION,
    HOSTS,
    MQTT_KEEPALIVE,
    OK_CODES,
    PATH_CERTIFICATION,
    PATH_DEVICE_LIST,
    PATH_QUOTA,
    PATH_QUOTA_ALL,
    TOPIC_QUOTA,
    TOPIC_SET,
    TOPIC_STATUS,
)

_LOGGER = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# TLS / session
# ─────────────────────────────────────────────
def make_ssl_context() -> ssl.SSLContext:
    """SSL context seeded from certifi so TLS verifies on Homey's runtime."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001 - fall back to system store if present
        return ssl.create_default_context()


def new_session() -> aiohttp.ClientSession:
    """An aiohttp session that can verify TLS on Homey."""
    return aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=make_ssl_context()))


# ─────────────────────────────────────────────
# HMAC signing
# ─────────────────────────────────────────────
def _flatten(obj: Any, prefix: str = "") -> dict[str, str]:
    """Flatten nested params per EcoFlow's signature spec.

    {"a": {"b": 1}} -> {"a.b": "1"};  {"ids": [1, 2]} -> {"ids[0]": "1", ...}.
    Booleans render lowercase; other scalars as plain strings.
    """
    items: dict[str, str] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            items.update(_flatten(v, key))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            items.update(_flatten(v, f"{prefix}[{i}]"))
    else:
        items[prefix] = "true" if obj is True else "false" if obj is False else str(obj)
    return items


def sign_headers(
    access_key: str, secret_key: str, params: dict | None = None
) -> dict[str, str]:
    """Build the accessKey/nonce/timestamp/sign headers for one request.

    The sign string is the flattened+ASCII-sorted params, then
    `accessKey=..&nonce=..&timestamp=..`, HMAC-SHA256'd with the secret key and
    hex-encoded (lowercase).
    """
    nonce = str(random.randint(100000, 999999))
    timestamp = str(int(time.time() * 1000))
    parts: list[str] = []
    if params:
        flat = _flatten(params)
        parts.extend(f"{k}={flat[k]}" for k in sorted(flat))
    parts.append(f"accessKey={access_key}")
    parts.append(f"nonce={nonce}")
    parts.append(f"timestamp={timestamp}")
    sign = hmac.new(
        secret_key.encode("utf-8"), "&".join(parts).encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return {
        "accessKey": access_key,
        "nonce": nonce,
        "timestamp": timestamp,
        "sign": sign,
        "Content-Type": "application/json;charset=UTF-8",
    }


# ─────────────────────────────────────────────
# REST client
# ─────────────────────────────────────────────
class EcoFlowClient:
    """Signed REST client for the EcoFlow IoT Open Platform."""

    def __init__(
        self,
        access_key: str,
        secret_key: str,
        session: aiohttp.ClientSession,
        region: str = DEFAULT_REGION,
    ) -> None:
        self._ak = access_key
        self._sk = secret_key
        self._session = session
        self._base = HOSTS.get(region, HOSTS[DEFAULT_REGION])
        self.region = region

    async def _request(
        self, method: str, path: str, params: dict | None = None
    ) -> Any:
        # EcoFlow's GET signature does NOT include query params (proven against
        # the live API 2026-08-27 — signing them returns 8521 "signature is
        # wrong"); only accessKey/nonce/timestamp are signed. POST/PUT bodies are
        # signed per the docs (untested — dev-API writes are currently blocked).
        sign_params = None if method == "GET" else params
        headers = sign_headers(self._ak, self._sk, sign_params)
        url = self._base + path
        kwargs: dict[str, Any] = {"headers": headers}
        if method == "GET" and params:
            kwargs["params"] = {k: params[k] for k in sorted(params)}
        elif method in ("POST", "PUT") and params is not None:
            kwargs["data"] = json.dumps(params)
        try:
            async with self._session.request(method, url, **kwargs) as r:
                text = await r.text()
                if r.status >= 400:
                    raise EcoFlowAPIError(f"{method} {path} -> {r.status}: {text[:300]}")
                try:
                    data = json.loads(text)
                except json.JSONDecodeError as e:
                    raise EcoFlowAPIError(f"{path}: bad JSON: {text[:200]}") from e
        except aiohttp.ClientError as e:
            raise EcoFlowAPIError(f"{method} {path} network error: {e}") from e

        code = str(data.get("code")) if isinstance(data, dict) else None
        if code not in OK_CODES:
            msg = data.get("message") if isinstance(data, dict) else None
            if code in ("401", "6042", "7005") or (msg and "sign" in str(msg).lower()):
                raise EcoFlowAuthError(f"{path}: auth/sign rejected: code={code} {msg!r}")
            raise EcoFlowAPIError(f"{path}: code={code} message={msg!r}")
        return data.get("data")

    # ── endpoints ──────────────────────────────────────────────
    async def get_device_list(self) -> list[dict]:
        """Every device bound to the account: {sn, online, productName, ...}."""
        data = await self._request("GET", PATH_DEVICE_LIST)
        return data if isinstance(data, list) else []

    async def get_quota_all(self, sn: str) -> dict:
        """Full live field map for one device."""
        data = await self._request("GET", PATH_QUOTA_ALL, {"sn": sn})
        return data if isinstance(data, dict) else {}

    async def set_function(self, sn: str, command: dict) -> Any:
        """Send a control command (PUT device/quota).

        `command` is the device-specific body without the sn, e.g.
        {"cmdCode": "...", "params": {...}} or {"cmdSet": 11, "id": 24,
        "params": {...}} depending on the model — finalized once we see the live
        set schema for the Panel 3 / Delta Pro Ultra X.
        """
        body = {"sn": sn, **command}
        return await self._request("PUT", PATH_QUOTA, body)

    async def get_certification(self) -> dict:
        """MQTT broker coordinates + credentials for the live push channel.

        Returns {url, port, protocol, certificateAccount, certificatePassword}.
        """
        data = await self._request("GET", PATH_CERTIFICATION)
        return data if isinstance(data, dict) else {}


# ─────────────────────────────────────────────
# MQTT live-quota subscriber
# ─────────────────────────────────────────────
class EcoFlowMqtt:
    """Persistent MQTT/TLS subscriber for real-time quota pushes.

    paho is synchronous, so the network loop runs in its own thread; incoming
    messages are marshalled back onto the asyncio loop via
    `loop.call_soon_threadsafe`. `on_quota(sn, params)` is invoked for every
    quota push (params is the flat field->value dict the cloud sends).
    """

    def __init__(
        self,
        cert: dict,
        loop: asyncio.AbstractEventLoop,
        on_quota: Callable[[str, dict], None],
        on_status: Callable[[str, dict], None] | None = None,
    ) -> None:
        self._cert = cert
        self._loop = loop
        self._on_quota = on_quota
        self._on_status = on_status
        self._client = None
        self._sns: set[str] = set()
        self._lock = threading.Lock()

    @property
    def account(self) -> str:
        return str(self._cert.get("certificateAccount") or "")

    def _topics(self, sn: str) -> list[str]:
        acct = self.account
        return [
            TOPIC_QUOTA.format(account=acct, sn=sn),
            TOPIC_STATUS.format(account=acct, sn=sn),
        ]

    def start(self, sns: list[str]) -> None:
        import paho.mqtt.client as mqtt

        self._sns = set(sns)
        acct = self.account
        client_id = f"HOMEY_{acct}_{int(time.time())}"
        try:
            client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION1,
                client_id=client_id,
                protocol=mqtt.MQTTv311,
            )
        except (AttributeError, TypeError):  # paho 1.x
            client = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311)

        client.username_pw_set(acct, password=str(self._cert.get("certificatePassword") or ""))
        client.tls_set_context(make_ssl_context())
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        host = str(self._cert.get("url") or "")
        port = int(self._cert.get("port") or 8883)
        self._client = client
        client.connect_async(host, port, keepalive=MQTT_KEEPALIVE)
        client.loop_start()
        _LOGGER.debug("EcoFlow MQTT connecting to %s:%s as %s", host, port, acct)

    def _on_connect(self, client, _u, _flags, rc, *_a):
        if rc not in (0, None):
            _LOGGER.error("EcoFlow MQTT connect refused rc=%s", rc)
            return
        with self._lock:
            for sn in self._sns:
                for topic in self._topics(sn):
                    client.subscribe(topic, qos=1)
        _LOGGER.debug("EcoFlow MQTT subscribed for %d device(s)", len(self._sns))

    def _on_message(self, _client, _u, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        parts = msg.topic.split("/")
        sn = parts[-2] if len(parts) >= 2 else ""
        kind = parts[-1]
        params = payload.get("params") if isinstance(payload, dict) else None
        if kind == "quota" and isinstance(params, dict):
            self._loop.call_soon_threadsafe(self._on_quota, sn, params)
        elif kind == "status" and self._on_status is not None:
            data = payload.get("params") if isinstance(payload, dict) else payload
            self._loop.call_soon_threadsafe(self._on_status, sn, data or {})

    def stop(self) -> None:
        if self._client is not None:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            self._client = None


# ─────────────────────────────────────────────
# device-list helpers
# ─────────────────────────────────────────────
def sn_of(device: dict) -> str:
    return str(device.get("sn") or "")


def name_of(device: dict) -> str:
    return str(
        device.get("deviceName") or device.get("productName") or device.get("sn") or "EcoFlow Device"
    )


def product_of(device: dict) -> str:
    return str(device.get("productName") or "")


def is_online(device: dict) -> bool:
    val = device.get("online")
    return val in (1, "1", True)


# ─────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────
class EcoFlowError(Exception):
    """Base EcoFlow error."""


class EcoFlowAuthError(EcoFlowError):
    """AccessKey/SecretKey rejected or signature invalid."""


class EcoFlowAPIError(EcoFlowError):
    """API call failed."""
