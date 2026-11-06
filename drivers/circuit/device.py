"""EcoFlow panel circuit switch — on/off + power for one circuit.

Piggybacks on the already-connected Smart Home Panel 3 device (shared MQTT):
reads its circuit's power from the panel's telemetry and sends on/off commands
through the panel. No own cloud connection.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

_APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)

from homey.device import Device  # noqa: E402

from ecoflow import registry  # noqa: E402

# ch_N_vol >= this ⇒ the circuit is energised (relay closed / ON).
_ON_VOLTAGE = 50.0


class CircuitDevice(Device):
    async def on_init(self) -> None:
        store = self.get_store()
        self.panel_sn = store.get("panel_sn")
        self.circuit = int(store.get("circuit") or 0)
        self.partner = int(store.get("partner") or 0)
        # A 240V appliance switches both legs together in one command.
        self._circuits = [self.circuit] + ([self.partner] if self.partner else [])
        self._loop = asyncio.get_running_loop()
        self._panel = None
        self._shutting_down = False

        self.register_capability_listener("onoff", self._on_set_onoff)
        self._attach_task = self._loop.create_task(self._attach())
        self.log(f"Circuit {self.circuit} device initialized (panel {self.panel_sn})")

    async def on_uninit(self) -> None:
        self._shutting_down = True
        if self._panel is not None:
            self._panel.unregister_circuit(self.circuit, self._on_panel_update)
        task = getattr(self, "_attach_task", None)
        if task:
            task.cancel()

    async def on_deleted(self) -> None:
        await self.on_uninit()

    # ── Attach to the panel device (it may init slightly later) ───────────────
    async def _attach(self) -> None:
        for _ in range(90):
            if self._shutting_down:
                return
            panel = registry.get_panel(self.panel_sn)
            if panel is not None:
                self._panel = panel
                params = panel.register_circuit(self.circuit, self._on_panel_update)
                await self.set_available()
                if params:
                    await self._apply(params)
                self.log(f"Circuit {self.circuit} attached to panel {self.panel_sn}")
                return
            await asyncio.sleep(2)
        await self.set_unavailable(
            "Smart Home Panel 3 not found — add the panel device first, same account."
        )

    def _on_panel_update(self, params: dict) -> None:
        # Called on the event loop by the panel's map_params.
        self._loop.create_task(self._apply(params))

    async def _apply(self, params: dict) -> None:
        pwr = params.get(f"ch_{self.circuit}_pwr")
        vol = params.get(f"ch_{self.circuit}_vol")
        if isinstance(pwr, (int, float)):
            await self._safe_set("measure_power", round(float(pwr)))
        if isinstance(vol, (int, float)):
            await self._safe_set("onoff", float(vol) >= _ON_VOLTAGE)

    # ── Control ────────────────────────────────────────────────────────────────
    async def _on_set_onoff(self, value: bool, **kwargs) -> None:
        if self._panel is None:
            self._panel = registry.get_panel(self.panel_sn)
        if self._panel is None:
            raise Exception("Panel not connected yet — try again in a moment.")
        ok = await self._panel.send_circuit(self._circuits, bool(value))
        if not ok:
            raise Exception("Could not send the command to the panel.")

    # ── Helper ────────────────────────────────────────────────────────────────
    async def _safe_set(self, cap: str, val: Any) -> None:
        if not self.has_capability(cap):
            return
        try:
            if self.get_capability_value(cap) != val:
                await self.set_capability_value(cap, val)
        except Exception as e:  # noqa: BLE001
            self.error(f"set {cap} failed:", e)


homey_export = CircuitDevice
