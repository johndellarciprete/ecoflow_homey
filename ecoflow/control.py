"""Build EcoFlow Smart Home Panel 3 circuit on/off commands (protobuf).

Reverse-engineered from a captured app command (2026-09-04). A circuit toggle is
a DP3Header with cmd_func=254, cmd_id=17 and a payload `{<control_id>: {1: v}}`,
v=1 ON / v=2 OFF, published to `/app/{userId}/{sn}/thing/property/set`. The panel
acks on `.../set_reply` (cmd_id 18) with `{1: control_id, 2: 1}`.

Circuit N maps to control_id `CONTROL_BASE + N` (circuit 32 -> 418 was captured,
and there are 32 circuits, so base = 386). `build_circuit_command` reproduces the
captured command byte-for-byte (see the self-test at the bottom).
"""

from __future__ import annotations

CONTROL_BASE = 386          # control_id = CONTROL_BASE + circuit_number
CIRCUITS = 32
VALUE_ON, VALUE_OFF = 1, 2


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _tag(field: int, wire: int) -> bytes:
    return _varint((field << 3) | wire)


def _fv(field: int, val: int) -> bytes:      # varint field
    return _tag(field, 0) + _varint(val)


def _fl(field: int, data: bytes) -> bytes:   # length-delimited field
    return _tag(field, 2) + _varint(len(data)) + data


def _fs(field: int, s: str) -> bytes:        # string field
    return _fl(field, s.encode("utf-8"))


def control_id_for(circuit: int) -> int:
    return CONTROL_BASE + circuit


def build_circuit_command(sn: str, circuits, on: bool, seq: int) -> bytes:
    """Return the MQTT payload to switch one or more panel circuits on/off.

    `circuits` is a circuit number or a list of them — a 240V appliance is a pair
    of legs switched together in a single message (as the EcoFlow app does).
    Publish this to `/app/{userId}/{sn}/thing/property/set`.
    """
    if isinstance(circuits, int):
        circuits = [circuits]
    value = VALUE_ON if on else VALUE_OFF
    # {control_id: {1: value}} for each leg, in ascending control-id order.
    pdata = b"".join(
        _fl(control_id_for(c), _fv(1, value)) for c in sorted(circuits)
    )
    header = (
        _fl(1, pdata)
        + _fv(2, 32)          # src
        + _fv(3, 11)          # dest
        + _fv(4, 1)
        + _fv(5, 1)
        + _fv(7, 3)           # check_type
        + _fv(8, 254)         # cmd_func
        + _fv(9, 17)          # cmd_id (circuit control)
        + _fv(10, len(pdata))  # data_len
        + _fv(11, 1)          # need_ack
        + _fv(14, seq & 0xFFFF)  # seq
        + _fv(16, 4)          # version
        + _fv(17, 1)          # payload_ver
        + _fs(23, "ios")      # from (client tag)
        + _fs(25, sn)         # device_sn
        + _fs(26, sn)
        + _fs(27, sn)
    )
    return _fl(1, header)     # DP3SendHeaderMsg { msg = 1 }


if __name__ == "__main__":  # self-test: reproduce the captured commands
    SN = "HR63ZA1AVJ1E0369"
    on_hex = build_circuit_command(SN, 32, True, 255).hex()
    off_hex = build_circuit_command(SN, 32, False, 246).hex()
    captured_on = ("0a620a05921a0208011020180b20012801380340fe0148115005580170ff01"
                   "800104880101ba0103696f73ca0110485236335a413141564a314530333639"
                   "d20110485236335a413141564a314530333639da0110485236335a413141564a314530333639")
    captured_off = ("0a620a05921a0208021020180b20012801380340fe0148115005580170f601"
                    "800104880101ba0103696f73ca0110485236335a413141564a314530333639"
                    "d20110485236335a413141564a314530333639da0110485236335a413141564a314530333639")
    print("ON  match:", on_hex == captured_on)
    print("OFF match:", off_hex == captured_off)
    assert on_hex == captured_on and off_hex == captured_off, "byte mismatch!"
    print("control_id circuit 1 ->", control_id_for(1), " circuit 32 ->", control_id_for(32))

    # 240V pair (well pump = circuits 12 & 14), captured 2026-09-11.
    wp_off = build_circuit_command(SN, [12, 14], False, 0x5A).hex()
    wp_on = build_circuit_command(SN, [12, 14], True, 0x61).hex()
    cap_wp_off = ("0a660a0af21802080282190208021020180b20012801380340fe014811500a5801705a"
                  "800104880101ba0103696f73ca0110485236335a413141564a314530333639"
                  "d20110485236335a413141564a314530333639da0110485236335a413141564a314530333639")
    cap_wp_on = ("0a660a0af21802080182190208011020180b20012801380340fe014811500a58017061"
                 "800104880101ba0103696f73ca0110485236335a413141564a314530333639"
                 "d20110485236335a413141564a314530333639da0110485236335a413141564a314530333639")
    print("240V OFF match:", wp_off == cap_wp_off)
    print("240V ON  match:", wp_on == cap_wp_on)
    assert wp_off == cap_wp_off and wp_on == cap_wp_on, "240V byte mismatch!"
