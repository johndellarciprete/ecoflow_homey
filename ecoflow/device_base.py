"""Shared EcoFlow device runtime (private app-cloud, MQTT + protobuf).

Signs in with the stored email/password, opens the private MQTT channel, and
feeds every decoded protobuf push through `map_params()` (implemented per model).
The official quota/all HTTP poll is unavailable (error 1006), so MQTT is the only
channel: EcoFlow sends a full upload periodically plus fast incremental deltas,
which we merge into a running params dict. A watchdog re-connects if the stream
goes quiet (token/cert expiry) and marks the device unavailable if it stays quiet.
"""

from __future__ import annotations

import asyncio
from typing import Any

from homey.device import Device

from .cloud import EcoFlowCloud, EcoFlowMqtt
from .api import EcoFlowAuthError, EcoFlowError, new_session
from .proto import decode_message

STALE_AFTER_S = 360      # no data this long -> try to reconnect
UNAVAILABLE_AFTER_S = 900  # no data this long -> mark unavailable
WATCHDOG_INTERVAL_S = 60


class EcoFlowDeviceBase(Device):
    LABEL = "EcoFlow device"

    async def on_init(self) -> None:
        store = self.get_store()
        self.sn = store.get("sn") or self.get_data().get("id")
        self._email = store.get("email")
        self._password = store.get("password")
        self._region = store.get("region") or "us"

        self._loop = asyncio.get_running_loop()
        self._shutting_down = False
        self._params: dict[str, Any] = {}
        self._mqtt: EcoFlowMqtt | None = None
        self._last_msg = 0.0        # loop.time() of last message for THIS sn
        self._wd_task: asyncio.Task | None = None
        self._get_task: asyncio.Task | None = None

        if not self._email or not self._password:
            await self.set_unavailable("Missing EcoFlow sign-in — please re-pair this device.")
            return

        self._session = new_session()
        self._cloud = EcoFlowCloud(self._email, self._password, self._session, region=self._region)
        self._decoder = self._build_decoder()

        try:
            await self._connect()
        except EcoFlowAuthError as e:
            self.error("EcoFlow sign-in failed:", e)
            await self.set_unavailable("EcoFlow sign-in failed — please re-pair this device.")
            return
        except EcoFlowError as e:
            self.error("EcoFlow connect failed:", e)

        self._wd_task = self._loop.create_task(self._watchdog())
        self._get_task = self._loop.create_task(self._get_loop())
        self.log(f"{self.LABEL} initialized: sn={self.sn}")

    async def on_uninit(self) -> None:
        self._shutting_down = True
        if self._mqtt:
            self._mqtt.stop()
        if self._wd_task:
            self._wd_task.cancel()
        if self._get_task:
            self._get_task.cancel()
        if getattr(self, "_session", None) and not self._session.closed:
            await self._session.close()

    async def on_deleted(self) -> None:
        await self.on_uninit()

    # ── Connection ──────────────────────────────────────────────────────────
    async def _connect(self) -> None:
        await self._cloud.login()
        cert = await self._cloud.get_certification()
        if self._mqtt:
            self._mqtt.stop()
        self._mqtt = EcoFlowMqtt(
            cert, self._cloud.user_id, self._loop, self._on_params, [self.sn],
            log=self.log, decoder=self._decoder,
        )
        self._mqtt.start()
        self.log(f"{self.LABEL}: MQTT channel open (user {self._cloud.user_id}).")

    async def _get_loop(self) -> None:
        """Periodically request a full quota push so data flows without the app."""
        await asyncio.sleep(4)  # let the first connection settle
        while not self._shutting_down:
            try:
                if self._mqtt and self._mqtt.connected:
                    self._mqtt.send_get(self.sn)
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.error("get loop error:", e)
                await asyncio.sleep(30)

    async def _watchdog(self) -> None:
        while not self._shutting_down:
            try:
                await asyncio.sleep(WATCHDOG_INTERVAL_S)
                if self._last_msg == 0.0:
                    continue  # nothing yet; give the first full-upload time
                age = self._loop.time() - self._last_msg
                if age > UNAVAILABLE_AFTER_S:
                    await self.set_unavailable("No data from EcoFlow — device may be offline.")
                if age > STALE_AFTER_S:
                    self.log(f"{self.LABEL}: stream quiet {int(age)}s, reconnecting.")
                    try:
                        await self._connect()
                    except EcoFlowError as e:
                        self.error("reconnect failed:", e)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.error("watchdog error:", e)

    def _on_params(self, sn: str, params: dict) -> None:
        """Runs on the loop thread for every decoded push (all devices)."""
        if sn and self.sn and sn != self.sn:
            return  # another device on the account
        self._last_msg = self._loop.time()
        self._params.update(params)
        self._loop.create_task(self._apply())

    async def _apply(self) -> None:
        try:
            await self.set_available()
            await self.map_params(self._params)
        except Exception as e:  # noqa: BLE001
            self.error("map_params error:", e)

    # ── Overridden per model ──────────────────────────────────────────────────
    def _build_decoder(self):
        """The protobuf decoder for this model. Panel overrides with its own."""
        return decode_message

    async def map_params(self, params: dict) -> None:
        return

    # ── Helper ────────────────────────────────────────────────────────────────
    async def _safe_set(self, cap: str, value: Any) -> None:
        if value is None or not self.has_capability(cap):
            return
        try:
            if self.get_capability_value(cap) != value:
                await self.set_capability_value(cap, value)
        except Exception as e:  # noqa: BLE001
            self.error(f"set {cap} failed:", e)
