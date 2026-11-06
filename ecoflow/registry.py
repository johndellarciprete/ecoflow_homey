"""Tiny in-process registry so circuit switch devices can find their panel.

Circuit devices don't open their own cloud connection — they piggyback on the
already-connected Smart Home Panel 3 device (shared MQTT), keyed by panel serial.
The panel registers itself here on init; circuits look it up.
"""

from __future__ import annotations

from typing import Any

_PANELS: dict[str, Any] = {}


def register_panel(sn: str, device: Any) -> None:
    if sn:
        _PANELS[sn] = device


def unregister_panel(sn: str, device: Any) -> None:
    if _PANELS.get(sn) is device:
        _PANELS.pop(sn, None)


def get_panel(sn: str) -> Any | None:
    return _PANELS.get(sn)


def panel_sns() -> list[str]:
    return list(_PANELS.keys())
