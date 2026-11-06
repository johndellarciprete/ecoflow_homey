"""Smart Home Panel 3 protobuf second-pass decoder.

The SHP3 speaks the Delta Pro 3 dialect (same header/cmdId routing), so the base
fields (cms_batt_soc, etc.) decode via `proto.decode_message`. The panel-specific
data — a 32-circuit load array, aggregate grid/load flows, and circuit labels —
lives in DisplayPropertyUpload (cmdId 21) fields the DP3 proto doesn't declare, so
it's recovered here with a raw second pass, following tolwi/hassio-ecoflow-cloud's
`smart_home_panel_3` decoders.

Circuit labels and 240V-partner links rotate in over several frames, so a
`PanelDecoder` instance holds that metadata across messages (one per device).
"""

from __future__ import annotations

import struct
from typing import Any

from .proto import _I32, _LEN, _VARINT, _iter_fields, decode_message, iter_pdata

# Aggregate flow / grid per-leg fields (all float, wire type 5).
F_GRID_PWR, F_LOAD_PWR = 515, 516
F_GRID_L1_PWR, F_GRID_L2_PWR = 962, 963
F_L1_VOL, F_L2_VOL = 956, 957
F_GRID_L1_AMP, F_GRID_L2_AMP = 958, 959


def _fieldmap(b: bytes) -> dict[int, list[tuple[int, Any]]]:
    out: dict[int, list[tuple[int, Any]]] = {}
    for fn, wt, raw in _iter_fields(b):
        out.setdefault(fn, []).append((wt, raw))
    return out


def _f32(fields: dict, no: int) -> float | None:
    for wt, raw in fields.get(no, []):
        if wt == _I32:
            return struct.unpack("<f", raw)[0]
    return None


def _len(fields: dict, no: int) -> bytes | None:
    for wt, raw in fields.get(no, []):
        if wt == _LEN:
            return raw
    return None


def _varint(fields: dict, no: int) -> int | None:
    for wt, raw in fields.get(no, []):
        if wt == _VARINT:
            return raw
    return None


class PanelDecoder:
    CIRCUITS = 32
    # Circuit metadata submessages (sub-field 5 = label, sub-field 2 = 240V
    # link), in power-array order: circuit N (1-based) -> NAME_FIELDS[N-1].
    NAME_FIELDS = list(range(794, 806)) + list(range(920, 940))
    CIRCUIT_FIELD_BASE = 1015

    def __init__(self) -> None:
        self._meta: dict[int, dict[str, Any]] = {}  # n -> {name, partner}
        self._flows: dict[str, float] = {}

    def decode(self, raw: bytes) -> dict[str, Any]:
        params = decode_message(raw)  # base DP3 fields + ids
        for cmd_id, pdata, _ids in iter_pdata(raw):
            if cmd_id == 21:
                try:
                    fields = _fieldmap(pdata)
                    self._decode_flows(fields, params)
                    self._decode_circuits(fields, params)
                    self._decode_metadata(fields, params)
                    self._combine_split_phase(params)
                except Exception:  # noqa: BLE001 - never break the base decode
                    pass
        return params

    def _decode_flows(self, fields: dict, result: dict) -> None:
        load = _f32(fields, F_LOAD_PWR)
        if load is not None:
            result["shp_load_pwr"] = round(load, 2)
            result["shp_grid_pwr"] = round(_f32(fields, F_GRID_PWR) or 0.0, 2)
        for key, no in (
            ("shp_grid_l1_pwr", F_GRID_L1_PWR),
            ("shp_grid_l2_pwr", F_GRID_L2_PWR),
            ("shp_l1_vol", F_L1_VOL),
            ("shp_l2_vol", F_L2_VOL),
            ("shp_grid_l1_amp", F_GRID_L1_AMP),
            ("shp_grid_l2_amp", F_GRID_L2_AMP),
        ):
            v = _f32(fields, no)
            if v is not None:
                result[key] = round(v, 2)
        for k in ("shp_grid_pwr", "shp_load_pwr"):
            if k in result:
                self._flows[k] = result[k]
        if len(self._flows) == 2:
            result["shp_batt_pwr"] = round(self._flows["shp_load_pwr"] - self._flows["shp_grid_pwr"], 2)

    def _decode_circuits(self, fields: dict, result: dict) -> None:
        for i in range(self.CIRCUITS):
            entry = _len(fields, self.CIRCUIT_FIELD_BASE + i)
            if entry is None:
                continue
            sub = _fieldmap(entry)
            n = i + 1
            vol = _f32(sub, 1)
            watt = _f32(sub, 2)
            amp = _f32(sub, 3)
            result[f"ch_{n}_vol"] = round(vol, 2) if vol is not None else 0.0
            # EcoFlow signs branch consumption negative; negate + clamp.
            result[f"ch_{n}_pwr"] = max(round(-watt, 2), 0.0) if watt is not None else 0.0
            result[f"ch_{n}_amp"] = round(amp, 2) if amp is not None else 0.0

    def _decode_metadata(self, fields: dict, result: dict) -> None:
        for n, field_no in enumerate(self.NAME_FIELDS, 1):
            entry = _len(fields, field_no)
            if entry is None:
                continue
            meta = _fieldmap(entry)
            label = _len(meta, 5)
            if label:
                try:
                    self._meta.setdefault(n, {})["name"] = label.decode("utf-8").strip().strip("\x00")
                except UnicodeDecodeError:
                    pass
            link = _len(meta, 2)
            if link is not None:
                partner = _varint(_fieldmap(link), 2)
                if partner is not None:
                    self._meta.setdefault(n, {})["partner"] = partner
        for n, m in self._meta.items():
            if "name" in m:
                result[f"ch_{n}_name"] = m["name"]
            if "partner" in m:
                result[f"ch_{n}_partner"] = m["partner"]

    def _combine_split_phase(self, result: dict) -> None:
        for n, m in self._meta.items():
            p = m.get("partner")
            if p and n < p and f"ch_{n}_pwr" in result and f"ch_{p}_pwr" in result:
                result[f"ch_{n}_pwr"] = round(result[f"ch_{n}_pwr"] + result[f"ch_{p}_pwr"], 2)
                result[f"ch_{p}_pwr"] = 0.0
