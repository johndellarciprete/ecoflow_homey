"""Shared pairing flow for the EcoFlow drivers (private app-cloud).

The user signs in with their EcoFlow app email + password (same as the phone
app). We validate the login, then DISCOVER devices by briefly listening on MQTT
and bucketing the decoded protobuf by serial number — the private cloud has no
device-list endpoint. Each driver supplies a `match(sn, params)` predicate so the
Delta driver only offers power stations and the panel driver only offers panels.

Credentials (email/password/region) are the user's own and are stored per device,
like the Rheem app. Nothing is baked into the package.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from homey.driver import ListDeviceProperties

from .cloud import EcoFlowCloud, EcoFlowMqtt
from .api import EcoFlowAuthError, EcoFlowError, new_session

# match(sn, sample_params) -> bool
Matcher = Callable[[str, dict], bool]

DISCOVERY_SECONDS = 25


async def _probe_sn(cloud: EcoFlowCloud, loop, sn: str, log=None) -> dict:
    """Subscribe the exact topic for `sn`, request data, and return its params.

    The private broker denies wildcard subscriptions, so we must target the
    serial directly. With the serial we can also publish a `latestQuotas` get to
    make an idle device report right away.
    """
    log = log or (lambda _m: None)
    cert = await cloud.get_certification()
    log(f"cert broker={cert.get('url')}:{cert.get('port')} account={cert.get('certificateAccount')}")
    found: dict[str, dict] = {}

    def on_params(msg_sn: str, params: dict) -> None:
        found.setdefault(msg_sn or sn, {}).update(params)

    mqtt = EcoFlowMqtt(cert, cloud.user_id, loop, on_params, [sn], log=log)
    mqtt.start()
    try:
        for _ in range(DISCOVERY_SECONDS):
            if mqtt.connected:
                mqtt.send_get(sn)
                break
            await asyncio.sleep(1)
        await asyncio.sleep(DISCOVERY_SECONDS)
    finally:
        log(f"probe done: connected={mqtt.connected} raw_msgs={mqtt.msg_count} "
            f"denied={mqtt.subscribe_denied} sns={list(found)}")
        denied = mqtt.subscribe_denied
        mqtt.stop()
    if denied and not found:
        # Plain Exception so this specific message reaches the pairing UI
        # (EcoFlowError would be swallowed by the generic handler).
        raise Exception(
            f"EcoFlow did not recognize serial '{sn}'. Check it is exactly right "
            "(usually 16 characters, no missing or extra digits) and try again."
        )
    return found.get(sn) or (next(iter(found.values()), {}) if found else {})


async def run_pair(driver, session, match: Matcher | None = None) -> None:
    driver._pair_seq = getattr(driver, "_pair_seq", 0) + 1
    sid = driver._pair_seq
    state: dict[str, Any] = {"creds": {}, "devices": {}}
    driver.log(f"[pair {sid}] session started")

    async def handle_login(data: dict) -> dict:
        email = ((data or {}).get("email") or "").strip()
        password = (data or {}).get("password") or ""
        region = ((data or {}).get("region") or "us").strip().lower()
        sn = ((data or {}).get("sn") or "").strip().upper()
        if not email or not password:
            raise Exception("Enter your EcoFlow email and password.")
        if not sn:
            raise Exception("Enter your device serial number (EcoFlow app → device → settings).")
        loop = asyncio.get_running_loop()
        try:
            async with new_session() as http:
                cloud = EcoFlowCloud(email, password, http, region=region)
                await cloud.login()
                params = await _probe_sn(cloud, loop, sn, log=driver.log)
        except EcoFlowAuthError as e:
            driver.error("EcoFlow login rejected:", e)
            raise Exception("Sign-in failed — check your EcoFlow email and password.") from e
        except EcoFlowError as e:
            driver.error("EcoFlow API error during pairing:", e)
            raise Exception("Could not reach EcoFlow. Try again in a moment.") from e

        if match is not None and not match(sn, params):
            driver.log(f"[pair {sid}] {sn} did not match this driver (params={list(params)[:8]})")
            raise Exception(
                "That serial signed in, but it doesn't look like this device type. "
                "Check you're adding the right serial for this driver."
            )
        state["creds"] = {"email": email, "password": password, "region": region}
        state["devices"] = {sn: params}
        driver.log(f"[pair {sid}] login OK, sn={sn}, got {len(params)} params")
        return {"count": 1, "streaming": bool(params)}

    async def handle_list_devices(_data: Any = None) -> list[ListDeviceProperties]:
        creds = state.get("creds") or {}
        out: list[ListDeviceProperties] = []
        for sn, params in state["devices"].items():
            name = _friendly_name(sn, params)
            out.append(
                {
                    "name": name,
                    "data": {"id": sn},
                    "store": {
                        "sn": sn,
                        "email": creds.get("email"),
                        "password": creds.get("password"),
                        "region": creds.get("region"),
                    },
                }
            )
        driver.log(f"[pair {sid}] list_devices -> {[o['name'] for o in out]}")
        return out

    session.set_handler("login", handle_login)
    session.set_handler("list_devices", handle_list_devices)


def _friendly_name(sn: str, params: dict) -> str:
    if sn.startswith("P101"):
        return "Delta Pro Ultra X"
    if sn.startswith("MT11"):
        return "Smart Home Panel 3"
    return sn


# ── Driver classifiers (content-based; SN prefix as a hint) ──────────────────
def is_power_station(sn: str, params: dict) -> bool:
    """A Delta-class power station reports combined battery SoC + pack/watt data."""
    return sn.startswith("P101") or "cms_batt_soc" in params or "pow_in_sum_w" in params


def is_panel(sn: str, params: dict) -> bool:
    """Panel classifier: known SHP serial prefixes, or panel-only flow fields."""
    return sn.startswith(("MT11", "HR63")) or "shp_load_pwr" in params
