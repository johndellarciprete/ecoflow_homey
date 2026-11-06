"""Pure-Python protobuf decoder for the EcoFlow Delta Pro 3 dialect.

No protobuf runtime (nothing to cross-compile for Homey): a tiny wire reader
driven by the field-number -> (name, kind) table in `proto_fields.py` (generated
from the vendored DP3 .proto). The Delta Pro Ultra X speaks this dialect.

An MQTT payload is `DP3SendHeaderMsg { repeated DP3Header msg = 1 }`. Each header
carries `cmd_id` (9), the `pdata` payload (1), and identifiers. cmd_id 21 =
DisplayPropertyUpload, 22 = RuntimePropertyUpload; both are PARTIAL updates, so
callers merge successive results. cmd_id 21 also carries the DPU-X per-pack array
at field 786 (absent from the DP3 proto), parsed here as a second pass.
"""

from __future__ import annotations

import struct
from typing import Any, Iterator

from .proto_fields import FIELD_MAPS

# Wire types
_VARINT, _I64, _LEN, _I32 = 0, 1, 2, 5


def _read_varint(b: bytes, i: int) -> tuple[int, int]:
    shift = result = 0
    while i < len(b):
        byte = b[i]
        i += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, i
        shift += 7
    raise ValueError("truncated varint")


def _iter_fields(b: bytes) -> Iterator[tuple[int, int, Any]]:
    """Yield (field_num, wire_type, raw) — raw is int (varint), or bytes/float."""
    i = 0
    n = len(b)
    while i < n:
        key, i = _read_varint(b, i)
        wt = key & 7
        fn = key >> 3
        if wt == _VARINT:
            val, i = _read_varint(b, i)
            yield fn, wt, val
        elif wt == _I64:
            yield fn, wt, b[i:i + 8]; i += 8
        elif wt == _LEN:
            ln, i = _read_varint(b, i)
            yield fn, wt, b[i:i + ln]; i += ln
        elif wt == _I32:
            yield fn, wt, b[i:i + 4]; i += 4
        else:
            raise ValueError(f"bad wire type {wt}")


def _as_int(v: int, signed: bool) -> int:
    if signed and v > 0x7FFFFFFFFFFFFFFF:
        return v - (1 << 64)
    return v


def _coerce(kind: str, wt: int, raw: Any) -> Any:
    """Interpret a raw wire value per the field's declared kind."""
    if kind == "float" and wt == _I32:
        return round(struct.unpack("<f", raw)[0], 4)
    if kind == "double" and wt == _I64:
        return struct.unpack("<d", raw)[0]
    if kind in ("uint", "ufixed32", "ufixed64"):
        if wt == _VARINT:
            return raw
        if wt == _I32:
            return struct.unpack("<I", raw)[0]
        if wt == _I64:
            return struct.unpack("<Q", raw)[0]
    if kind == "int" and wt == _VARINT:
        return _as_int(raw, signed=True)
    if kind == "sint" and wt == _VARINT:
        return (raw >> 1) ^ -(raw & 1)  # zigzag
    if kind == "string" and wt == _LEN:
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.hex()
    if kind == "bytes" and wt == _LEN:
        return raw
    # Fallback: hand back something usable.
    if wt == _VARINT:
        return raw
    if wt == _I32:
        return struct.unpack("<f", raw)[0]
    return raw


def _decode_pdata(cmd_id: int, pdata: bytes) -> dict[str, Any]:
    fmap = FIELD_MAPS.get(cmd_id, {})
    out: dict[str, Any] = {}
    for fn, wt, raw in _iter_fields(pdata):
        entry = fmap.get(fn)
        if not entry:
            continue  # unknown/unused field for this stream
        name, kind = entry
        if kind == "message":
            continue  # nested config (time tasks etc.) — not surfaced
        out[name] = _coerce(kind, wt, raw)
    return out


def _decode_pack_extra(pdata: bytes) -> dict[str, Any]:
    """DPU-X per-pack array at DisplayPropertyUpload field 786 (not in DP3 proto).

    786 = DPUXBpInfo{ repeated DPUXPack packs = 1 };
    DPUXPack{ bay=2, soc=4, amp=5, design=6, temp=8 }.
    """
    out: dict[str, Any] = {}
    for fn, wt, raw in _iter_fields(pdata):
        if fn != 786 or wt != _LEN:
            continue
        for pfn, pwt, praw in _iter_fields(raw):        # packs (repeated field 1)
            if pfn != 1 or pwt != _LEN:
                continue
            bay = soc = temp = None
            for k, kwt, kraw in _iter_fields(praw):
                if k == 2:
                    bay = kraw
                elif k == 4:
                    soc = kraw
                elif k == 8:
                    temp = _as_int(kraw, signed=True)
            if bay is not None:
                if soc is not None:
                    out[f"bp_{bay}_soc"] = soc
                if temp is not None:
                    out[f"bp_{bay}_temp"] = temp
    return out


def iter_pdata(raw: bytes):
    """Yield (cmd_id, pdata, ids) for each DP3Header in an MQTT payload.

    pdata is XOR-decoded when the header calls for it (enc_type==1, src!=32).
    ids carries device_sn/module_sn when present. Shared by the generic decoder
    and the device-specific second passes (DPU-X packs, SHP3 circuits).
    """
    header_map = FIELD_MAPS["header"]
    for _fn, _wt, hdr in _iter_fields(raw):           # DP3SendHeaderMsg.msg (field 1)
        if _fn != 1 or _wt != _LEN:
            continue
        pdata = b""
        cmd_id = None
        src = enc_type = seq = 0
        ids: dict[str, Any] = {}
        for hf, hwt, hraw in _iter_fields(hdr):
            name_kind = header_map.get(hf)
            if hf == 1 and hwt == _LEN:
                pdata = hraw
            elif hf == 2 and hwt == _VARINT:
                src = hraw
            elif hf == 6 and hwt == _VARINT:
                enc_type = hraw
            elif hf == 9 and hwt == _VARINT:
                cmd_id = hraw
            elif hf == 14 and hwt == _VARINT:
                seq = hraw
            elif name_kind and name_kind[0] in ("device_sn", "module_sn") and hwt == _LEN:
                try:
                    ids[name_kind[0]] = hraw.decode("utf-8")
                except UnicodeDecodeError:
                    pass
        if cmd_id is None or not pdata:
            continue
        if enc_type == 1 and src != 32:  # XOR'd with the low byte of seq
            k = seq & 0xFF
            pdata = bytes(b ^ k for b in pdata)
        yield cmd_id, pdata, ids


def decode_message(raw: bytes) -> dict[str, Any]:
    """Decode one MQTT payload into a flat {name: value} params dict.

    Merges every DP3Header record. Includes `device_sn` / `module_sn` when
    present and the per-pack bp_* keys from cmd_id 21.
    """
    params: dict[str, Any] = {}
    for cmd_id, pdata, ids in iter_pdata(raw):
        if cmd_id in FIELD_MAPS:
            params.update(_decode_pdata(cmd_id, pdata))
        if cmd_id == 21:
            params.update(_decode_pack_extra(pdata))
        params.update(ids)
    return params
