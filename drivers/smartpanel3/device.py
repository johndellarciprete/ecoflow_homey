"""EcoFlow Smart Home Panel 3 device — flows + per-leg, and the shared hub for
its 32 circuit switch devices (circuit power + on/off control)."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Any, Callable

_APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)

from ecoflow.device_base import EcoFlowDeviceBase  # noqa: E402
from ecoflow.mapping import panel_capabilities  # noqa: E402
from ecoflow.proto_panel import PanelDecoder  # noqa: E402
from ecoflow import registry  # noqa: E402
from ecoflow.control import build_circuit_command  # noqa: E402


class SmartPanel3Device(EcoFlowDeviceBase):
    LABEL = "EcoFlow Smart Home Panel 3"

    async def on_init(self) -> None:
        self._circuit_listeners: dict[int, list[Callable[[dict], None]]] = {}
        self._cmd_seq = int(time.time()) & 0xFFFF
        self._grid_online: bool | None = None
        await super().on_init()
        registry.register_panel(self.sn, self)
        # Added at runtime so existing panels get it without re-pairing.
        if not self.has_capability("ecoflow_grid_online"):
            try:
                await self.add_capability("ecoflow_grid_online")
            except Exception as e:  # noqa: BLE001
                self.error("add ecoflow_grid_online failed:", e)
        # The 32 circuit tiles now live on separate switch devices; drop any that
        # a previous version added to this panel device so it stays uncluttered.
        for cap in list(self.get_capabilities() if hasattr(self, "get_capabilities") else []):
            if cap.startswith("ecoflow_circuit_pwr."):
                try:
                    await self.remove_capability(cap)
                except Exception:  # noqa: BLE001
                    pass

    async def on_uninit(self) -> None:
        registry.unregister_panel(self.sn, self)
        await super().on_uninit()

    def _build_decoder(self):
        return PanelDecoder().decode  # stateful: circuit labels accumulate

    async def map_params(self, params: dict[str, Any]) -> None:
        # Aggregate tiles only (grid/load/battery/voltage); per-circuit data goes
        # to the circuit switch devices via listeners.
        for cap, value in panel_capabilities(params).items():
            if "." in cap:
                continue
            await self._safe_set(cap, value)
        await self._update_grid_online(params)
        # Notify circuit devices (each reads its own ch_N_* from params).
        for cbs in self._circuit_listeners.values():
            for cb in list(cbs):
                try:
                    cb(params)
                except Exception as e:  # noqa: BLE001
                    self.error("circuit listener error:", e)

    # ── API used by circuit switch devices ────────────────────────────────────
    def register_circuit(self, n: int, cb: Callable[[dict], None]) -> dict:
        self._circuit_listeners.setdefault(n, []).append(cb)
        return self._params  # hand over the latest so the circuit updates now

    def unregister_circuit(self, n: int, cb: Callable[[dict], None]) -> None:
        lst = self._circuit_listeners.get(n)
        if lst and cb in lst:
            lst.remove(cb)

    async def send_circuit(self, circuits, on: bool) -> bool:
        """Switch one circuit or a 240V pair (both legs in one message)."""
        if not self._mqtt:
            return False
        self._cmd_seq = (self._cmd_seq + 1) & 0xFFFF
        payload = build_circuit_command(self.sn, circuits, on, self._cmd_seq)
        ok = self._mqtt.publish_set(self.sn, payload)
        self.log(f"circuit {circuits} -> {'ON' if on else 'OFF'} ({'sent' if ok else 'FAILED'})")
        return ok

    def circuit_name(self, n: int) -> str | None:
        """The EcoFlow-app label for circuit n (base name, no leg suffix)."""
        nm = self._params.get(f"ch_{n}_name")
        return nm.strip() if isinstance(nm, str) and nm.strip() else None

    async def _update_grid_online(self, params: dict) -> None:
        """Grid on/off from the grid leg voltage (drops to ~0 during an outage);
        set the boolean capability and fire the Flow trigger on change."""
        l1 = params.get("shp_l1_vol")
        l2 = params.get("shp_l2_vol")
        if not isinstance(l1, (int, float)) and not isinstance(l2, (int, float)):
            return  # grid voltage not reported this frame — leave state as is
        online = max(float(l1 or 0), float(l2 or 0)) > 50.0
        if online == self._grid_online:
            return
        prev = self._grid_online
        self._grid_online = online
        await self._safe_set("ecoflow_grid_online", online)
        if prev is None:
            return  # first reading — don't fire a trigger
        card = "grid_power_on" if online else "grid_power_off"
        try:
            await self.homey.flow.get_device_trigger_card(card).trigger(self, {}, {})
            self.log(f"grid power {'ON' if online else 'OFF'} — fired {card}")
        except Exception as e:  # noqa: BLE001
            self.error("grid trigger failed:", e)

    def circuit_partner(self, n: int) -> int:
        """The 240V partner leg of circuit n, or 0 if it's a single 120V circuit."""
        p = self._params.get(f"ch_{n}_partner")
        return int(p) if isinstance(p, (int, float)) and p else 0

    def _name_count(self) -> int:
        return sum(1 for n in range(1, 33) if self.circuit_name(n))

    async def ensure_circuit_names(self, timeout: float = 15.0) -> int:
        """Make sure circuit labels are loaded before pairing names the switches.

        Labels + 240V partners arrive together in the get_reply, but across a few
        frames — so request a fresh full state and wait until the label count
        STOPS climbing (not just until the first one shows), or timeout. Returns
        how many labels are available."""
        if self._mqtt:
            self._mqtt.send_get(self.sn)
        deadline = self._loop.time() + timeout
        last = -1
        stable = 0
        while self._loop.time() < deadline:
            await asyncio.sleep(0.5)
            cur = self._name_count()
            if cur != last:
                last = cur
                stable = 0
            elif cur > 0:
                stable += 1
                if stable >= 6:   # ~3s with no new labels -> full set received
                    break
        self.log(f"ensure_circuit_names: {self._name_count()} labels after fetch")
        return self._name_count()


homey_export = SmartPanel3Device
