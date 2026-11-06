"""Pure param-dict -> Homey-capability mapping (no Homey imports = unit-testable).

Keeps the field-name knowledge in one place. `delta_capabilities` turns a decoded
Delta Pro Ultra X params dict into {capability_id: value}; the device just pushes
those onto Homey. A value of None means "don't set / clear".
"""

from __future__ import annotations

from typing import Any

_CHG_STATE = {0: "idle", 1: "discharging", 2: "charging"}
_REM_SENTINEL = 143999  # EcoFlow "no meaningful remaining time"


def _f(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fmt_minutes(mins: int) -> str:
    """22980 -> '15d 23h'; 270 -> '4h 30m'; 45 -> '45m'."""
    d, rem = divmod(int(mins), 1440)
    h, m = divmod(rem, 60)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def _state_and_time(p: dict[str, Any], caps: dict[str, Any]) -> None:
    """Shared charge-state + formatted time-remaining mapping."""
    state = p.get("cms_chg_dsg_state")
    if state is not None:
        caps["ecoflow_charge_state"] = _CHG_STATE.get(int(state), "idle")
    rem = p.get("cms_chg_rem_time") if state == 2 else p.get("cms_dsg_rem_time")
    if rem is None and state != 2:
        rem = p.get("cms_chg_rem_time")
    if rem is not None:
        rem = int(rem)
        caps["ecoflow_time_remaining"] = None if rem >= _REM_SENTINEL else _fmt_minutes(rem)


def panel_capabilities(p: dict[str, Any]) -> dict[str, Any]:
    """Smart Home Panel 3 params -> Homey capabilities."""
    caps: dict[str, Any] = {}

    soc = _f(p.get("cms_batt_soc"))
    if soc is not None:
        caps["measure_battery"] = round(soc)

    load = _f(p.get("shp_load_pwr"))
    if load is not None:
        caps["measure_power"] = round(load)          # home load = headline power
    grid = _f(p.get("shp_grid_pwr"))
    if grid is not None:
        caps["ecoflow_grid_pwr"] = round(grid)
    batt = _f(p.get("shp_batt_pwr"))
    if batt is not None:
        caps["ecoflow_batt_pwr"] = round(batt)

    l1 = _f(p.get("shp_l1_vol"))
    l2 = _f(p.get("shp_l2_vol"))
    if l1 is not None:
        caps["ecoflow_l1_voltage"] = round(l1, 1)
    if l2 is not None:
        caps["ecoflow_l2_voltage"] = round(l2, 1)

    _state_and_time(p, caps)

    # Per-circuit power (one subcapability per monitored circuit).
    for k, v in p.items():
        if k.startswith("ch_") and k.endswith("_pwr"):
            val = _f(v)
            if val is not None:
                caps[f"ecoflow_circuit_pwr.{k[3:-4]}"] = round(val)
    return caps


def delta_capabilities(p: dict[str, Any]) -> dict[str, Any]:
    caps: dict[str, Any] = {}

    soc = _f(p.get("cms_batt_soc"))
    if soc is not None:
        caps["measure_battery"] = round(soc)

    pin = _f(p.get("pow_in_sum_w"))
    pout = _f(p.get("pow_out_sum_w"))
    if pin is not None:
        caps["ecoflow_pow_in"] = round(pin)
    if pout is not None:
        caps["ecoflow_pow_out"] = round(pout)
    if pin is not None or pout is not None:
        caps["measure_power"] = round((pout or 0.0) - (pin or 0.0))

    # Charge state + remaining time (discharge runtime when idle, like the app).
    _state_and_time(p, caps)

    l1 = _f(p.get("plug_in_info_l1_vol"))
    l2 = _f(p.get("plug_in_info_l2_vol"))
    if l1 is not None:
        caps["ecoflow_l1_voltage"] = round(l1, 1)
    if l2 is not None:
        caps["ecoflow_l2_voltage"] = round(l2, 1)

    # AC output power (all AC ports combined).
    ac = _f(p.get("pow_get_ac"))
    if ac is not None:
        caps["ecoflow_ac_out"] = round(ac)

    # Solar input = the two PV circuits combined.
    pvh = _f(p.get("pow_get_pv_h"))
    pvl = _f(p.get("pow_get_pv_l"))
    if pvh is not None or pvl is not None:
        caps["ecoflow_solar_in"] = round((pvh or 0.0) + (pvl or 0.0))

    # System temperature = hottest of the inverter / LLC sensors.
    temps = [
        _f(p.get(k)) for k in ("inv_ntc_temp2", "inv_ntc_temp3", "llc_ntc_temp")
    ]
    temps = [t for t in temps if t is not None]
    if temps:
        caps["measure_temperature"] = round(max(temps))

    # Per-battery-pack SoC/temp (only bays that report — added as subcapabilities).
    for k, v in p.items():
        val = _f(v)
        if val is None:
            continue
        if k.startswith("bp_") and k.endswith("_soc"):
            caps[f"ecoflow_pack_soc.{k[3:-4]}"] = round(val)
        elif k.startswith("bp_") and k.endswith("_temp"):
            caps[f"ecoflow_pack_temp.{k[3:-5]}"] = round(val)

    return caps
