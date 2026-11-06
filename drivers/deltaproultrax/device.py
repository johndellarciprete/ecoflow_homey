"""EcoFlow Delta Pro Ultra X device — maps decoded protobuf to capabilities."""

from __future__ import annotations

import os
import sys
from typing import Any

_APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)

from ecoflow.device_base import EcoFlowDeviceBase  # noqa: E402
from ecoflow.mapping import delta_capabilities  # noqa: E402


class DeltaProUltraXDevice(EcoFlowDeviceBase):
    LABEL = "EcoFlow Delta Pro Ultra X"

    async def map_params(self, params: dict[str, Any]) -> None:
        for cap, value in delta_capabilities(params).items():
            if "." in cap and not self.has_capability(cap):
                await self._add_pack_capability(cap)
            await self._safe_set(cap, value)

    async def _add_pack_capability(self, cap: str) -> None:
        """Add a per-pack subcapability (e.g. ecoflow_pack_soc.3) with a title."""
        base, _, bay = cap.partition(".")
        kind = "charge" if base.endswith("soc") else "temperature"
        try:
            await self.add_capability(cap)
            await self.set_capability_options(
                cap, {"title": {"en": f"Pack {bay} {kind}"}}
            )
        except Exception as e:  # noqa: BLE001
            self.error(f"add pack capability {cap} failed:", e)


homey_export = DeltaProUltraXDevice
