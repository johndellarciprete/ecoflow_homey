"""EcoFlow panel circuit switch driver — creates the 32 circuit switches."""

from __future__ import annotations

import os
import sys

_APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)

from homey.driver import Driver  # noqa: E402

from ecoflow import registry  # noqa: E402

CIRCUITS = 32


class CircuitDriver(Driver):
    async def on_init(self) -> None:
        self.log("EcoFlow circuit switch driver initialized")

    async def on_pair(self, session) -> None:
        async def handle_panels(_data=None) -> list:
            # Auto-detect already-added panels so the user needn't type a serial.
            return [{"sn": sn} for sn in registry.panel_sns()]

        async def handle_list(data) -> list:
            sn = ((data or {}).get("sn") or "").strip().upper()
            if not sn:
                panels = registry.panel_sns()
                if len(panels) == 1:
                    sn = panels[0]
                else:
                    raise Exception("Add the Smart Home Panel 3 device first, or enter its serial.")
            # Name each switch from the panel's decoded EcoFlow label if the panel
            # device is present (Homey can only set a device name at creation, so
            # this is the one chance to bake the real names in). Labels arrive in
            # the get_reply, so ask the panel to fetch+wait for them first.
            panel = registry.get_panel(sn)
            if panel is not None:
                try:
                    await panel.ensure_circuit_names()
                except Exception as e:  # noqa: BLE001
                    self.log(f"ensure_circuit_names failed: {e}")
            avail = panel._name_count() if panel is not None else -1
            self.log(f"circuit pairing: panel found={panel is not None}, labels available={avail}")
            out = []
            for n in range(1, CIRCUITS + 1):
                partner = panel.circuit_partner(n) if panel is not None else 0
                # A 240V appliance is two legs; make ONE switch on the primary
                # (lower-numbered) leg and skip the secondary so there are no
                # duplicate-named devices.
                if partner and partner < n:
                    continue
                label = panel.circuit_name(n) if panel is not None else None
                store = {"panel_sn": sn, "circuit": n}
                if partner:
                    store["partner"] = partner
                out.append(
                    {
                        "name": label or f"Circuit {n}",
                        "data": {"id": f"{sn}-ch{n}"},
                        "store": store,
                    }
                )
            named = sum(1 for o in out if not o["name"].startswith("Circuit "))
            self.log(f"circuit pairing: offering {len(out)} switches for {sn} ({named} named)")
            return out

        session.set_handler("panels", handle_panels)
        session.set_handler("list", handle_list)


homey_export = CircuitDriver
