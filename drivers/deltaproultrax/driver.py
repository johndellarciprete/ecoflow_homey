"""EcoFlow Delta Pro Ultra X driver — AccessKey/SecretKey pairing."""

from __future__ import annotations

import os
import sys

# Make the app-root `ecoflow` package importable from this nested driver module.
_APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)

from homey.driver import Driver  # noqa: E402

from ecoflow.pairing import is_power_station, run_pair  # noqa: E402


class DeltaProUltraXDriver(Driver):
    async def on_init(self) -> None:
        self.log("EcoFlow Delta Pro Ultra X driver initialized")

    async def on_pair(self, session) -> None:
        await run_pair(self, session, match=is_power_station)


homey_export = DeltaProUltraXDriver
