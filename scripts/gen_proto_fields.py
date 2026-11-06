#!/usr/bin/env python3
"""Generate ecoflow/proto_fields.py from the vendored DP3 .proto.

We decode EcoFlow's protobuf with a tiny pure-Python wire reader (no protobuf
runtime to cross-compile for Homey), driven by a field-number -> (name, kind)
table. This script builds that table straight from `reference/ef_dp3_iobroker.proto`
so the mapping is exact and complete, not hand-typed.
"""
import os
import re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PROTO = os.path.join(ROOT, "reference", "ef_dp3_iobroker.proto")
OUT = os.path.join(ROOT, "ecoflow", "proto_fields.py")

# proto messages we decode, mapped to the cmdId that carries them.
MESSAGES = {
    "DP3DisplayPropertyUpload": 21,
    "DP3RuntimePropertyUpload": 22,
    "DP3Header": "header",
}

# proto scalar type -> decoder kind
SCALAR = {
    "float": "float", "double": "double",
    "uint32": "uint", "uint64": "uint", "fixed32": "ufixed32", "fixed64": "ufixed64",
    "int32": "int", "int64": "int", "sint32": "sint", "sint64": "sint",
    "bool": "uint", "bytes": "bytes", "string": "string",
}

FIELD_RE = re.compile(
    r"optional\s+(?P<type>[\w\.]+)\s+(?P<name>\w+)\s*=\s*(?P<num>\d+)\s*;"
)


def parse_message(text, name):
    m = re.search(r"message\s+" + re.escape(name) + r"\s*\{", text)
    if not m:
        return {}
    # find matching brace
    i = m.end()
    depth = 1
    while i < len(text) and depth:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    body = text[m.end():i]
    fields = {}
    for fm in FIELD_RE.finditer(body):
        t = fm.group("type")
        kind = SCALAR.get(t, "message")  # unknown = nested message -> skip on decode
        fields[int(fm.group("num"))] = (fm.group("name"), kind)
    return fields


def main():
    with open(PROTO, encoding="utf-8") as f:
        text = f.read()
    out = ['"""AUTO-GENERATED from reference/ef_dp3_iobroker.proto — do not edit by hand.',
           'Regenerate with scripts/gen_proto_fields.py."""', "", "FIELD_MAPS = {"]
    for msg, cmd in MESSAGES.items():
        fields = parse_message(text, msg)
        key = repr(cmd)
        out.append(f"    {key}: {{  # {msg} ({len(fields)} fields)")
        for num in sorted(fields):
            nm, kind = fields[num]
            out.append(f"        {num}: ({nm!r}, {kind!r}),")
        out.append("    },")
    out.append("}")
    out.append("")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    total = sum(len(parse_message(text, m)) for m in MESSAGES)
    print(f"Wrote {OUT} ({total} fields across {len(MESSAGES)} messages)")


if __name__ == "__main__":
    main()
