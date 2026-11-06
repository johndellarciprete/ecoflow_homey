# Tomorrow (Panel 3 install day) — checklist

The Smart Home Panel 3 driver is **fully pre-built and validated** against the
reference integration's real data. Once the panel is installed and online, this
should be a capture-verify-done flow.

## 1. Capture the panel's live protobuf (John runs)
With the panel online and the EcoFlow app open on it:
```
python "C:\Users\JohnDell'Arciprete\HomeyApps\com.dellarciprete.ecoflow\scripts\ecoflow_app_probe.py" johndellarciprete@gmail.com --seconds 120
```
The probe already targets the panel serial (`MT11ZA1BKHCA0413`) and now subscribes
the exact per-serial topic + requests a full push, so an idle panel still reports.
Look for `suback granted_qos = [1]` and `msg on /app/device/property/MT11… `.
Send me the new `ecoflow_app_msg_MT11*` dump files.

## 2. Verify the decode (Claude)
Decode the panel bytes with `PanelDecoder` and confirm the field numbers match:
- circuits at 1015+, names at 794–805 / 920–939, flows at 515/516/956–963.
If they match (expected — same SHP3 spec), the panel is done. If John's panel
(DPU-X-connected) numbers its fields differently, adjust the constants in
`ecoflow/proto_panel.py`.

## 3. Pair the panel on Homey
Add device → EcoFlow Whole-Home → **Smart Home Panel 3**, sign in, enter serial
`MT11ZA1BKHCA0413`. Tiles: battery %, home load (measure_power), grid power,
storage output, per-leg voltage, status, time remaining, plus **one power tile per
circuit** (added dynamically, titled by the app's circuit labels).

## What's already built for the panel
- `ecoflow/proto_panel.py` — `PanelDecoder` (circuits + flows + labels + 240V
  split-phase combine), mirroring tolwi's `smart_home_panel_3`.
- `ecoflow/mapping.py` `panel_capabilities()` — VALIDATED against tolwi's
  `diag_smart_home_panel_3.json` (battery 87, load 1598W, 32 circuits, etc.).
- `drivers/smartpanel3/` device (stateful decoder + per-circuit subcaps titled by
  label), driver, pair view.
- Manifest caps: ecoflow_grid_pwr, ecoflow_batt_pwr, ecoflow_circuit_pwr (+ icons
  grid/storage/circuit). Validates at publish level.

## Known risk / watch-for
- Circuit field numbers are from a different panel unit; John's may differ — the
  raw capture confirms/corrects them in one pass.
- Panel classification uses SN prefix (MT11/HR63) or `shp_load_pwr` in params.
