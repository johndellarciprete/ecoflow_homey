#!/usr/bin/env python3
"""Generic protobuf wire-format decoder for the captured EcoFlow messages.

No .proto needed: it walks the raw wire format and prints field numbers, wire
types, and best-effort value interpretations (varint, fixed32-as-float, nested
message, ascii string). This maps the DPU X envelope + payload so we can build a
typed decoder.
"""
import struct
import sys
import os
import glob

OUT = os.path.dirname(os.path.abspath(__file__))


def read_varint(b, i):
    shift = 0
    result = 0
    while i < len(b):
        byte = b[i]
        i += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, i
        shift += 7
    raise ValueError("truncated varint")


def looks_like_proto(b):
    """Heuristic: can we walk the whole buffer as valid protobuf fields?"""
    i = 0
    if len(b) == 0:
        return False
    try:
        while i < len(b):
            key, i = read_varint(b, i)
            wt = key & 7
            fn = key >> 3
            if fn == 0 or wt in (3, 4, 6, 7):
                return False
            if wt == 0:
                _, i = read_varint(b, i)
            elif wt == 1:
                i += 8
            elif wt == 2:
                ln, i = read_varint(b, i)
                i += ln
            elif wt == 5:
                i += 4
            if i > len(b):
                return False
        return i == len(b)
    except Exception:
        return False


def decode(b, indent=0, path=""):
    i = 0
    pad = "  " * indent
    while i < len(b):
        key, i = read_varint(b, i)
        wt = key & 7
        fn = key >> 3
        fp = f"{path}.{fn}" if path else str(fn)
        if wt == 0:
            v, i = read_varint(b, i)
            sv = ""
            # signed-zigzag alt view for negatives
            if v > 0x7FFFFFFFFFFFFFFF:
                sv = f"  (s64={v - (1 << 64)})"
            print(f"{pad}#{fp} varint = {v}{sv}")
        elif wt == 1:
            raw = b[i:i + 8]; i += 8
            d = struct.unpack("<d", raw)[0] if len(raw) == 8 else None
            u = struct.unpack("<Q", raw)[0] if len(raw) == 8 else None
            print(f"{pad}#{fp} fixed64 = u{u} / double={d}")
        elif wt == 2:
            ln, i = read_varint(b, i)
            sub = b[i:i + ln]; i += ln
            if looks_like_proto(sub) and len(sub) > 1:
                print(f"{pad}#{fp} message ({ln} bytes):")
                decode(sub, indent + 1, fp)
            else:
                try:
                    s = sub.decode("ascii")
                    printable = all(32 <= ord(c) < 127 for c in s)
                except Exception:
                    printable = False
                if printable and len(sub) > 0:
                    print(f"{pad}#{fp} string = {s!r}")
                elif len(sub) % 4 == 0 and len(sub) > 0:
                    floats = struct.unpack("<%df" % (len(sub) // 4), sub)
                    print(f"{pad}#{fp} bytes({ln}) packed floats/fixed32 = {floats}")
                else:
                    print(f"{pad}#{fp} bytes({ln}) = {sub.hex()}")
        elif wt == 5:
            raw = b[i:i + 4]; i += 4
            f = struct.unpack("<f", raw)[0]
            u = struct.unpack("<I", raw)[0]
            print(f"{pad}#{fp} fixed32 = u{u} / float={f:.4f}")
        else:
            print(f"{pad}#{fp} ??? wiretype {wt}")
            return


def main():
    files = sys.argv[1:] or sorted(glob.glob(os.path.join(OUT, "ecoflow_app_msg_*.hex.txt")))
    for path in files:
        with open(path) as f:
            hexstr = f.read().strip()
        b = bytes.fromhex(hexstr)
        print("=" * 70)
        print(os.path.basename(path), f"({len(b)} bytes)")
        print("=" * 70)
        decode(b)
        print()


if __name__ == "__main__":
    main()
