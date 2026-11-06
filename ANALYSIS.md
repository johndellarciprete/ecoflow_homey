# Delta Pro Ultra X (P101ZA1A9J470312) — protobuf analysis

Captured live 2026-08-27 via the **app-login private API** (login OK as `jdell42`,
userId `2086909725195612162`, broker `mqtt.ecoflow.com:8883`, cert account
`app-…`). Payloads are **protobuf** (not JSON). MT11 "PowerInsight" (Panel 3 side)
was offline, so no Panel 3 data yet.

## MQTT
- Data arrives on the private topic tree under `/app/{userId}/…` (subscribed
  `/app/{userId}/#` + `/app/device/property/{sn}`).
- One MQTT message can concatenate several `Header` records.

## Envelope (outer)  — `Envelope { repeated Header header = 1 }`
Each `Header`:
| field | meaning (inferred) | example |
|-------|--------------------|---------|
| 1 | **pdata** = the payload protobuf | (nested) |
| 2 | src | 2 |
| 3 | dest | 32 |
| 4 | d_src | 1 |
| 5 | d_dest | 1 |
| 7 | enc_type | 3 |
| 8 | check_type | 254 |
| **9** | **cmd_func / stream id** | 21 or 22 |
| 10 | data_len (== pdata length) | 280 |
| 14 | seq (increments) | 4522668 |
| 15 | product_id | 8705 |
| 16 | version | 3 |
| 24 | **device_sn** (ascii) | "P101ZA1A9J470312" |

Two payload streams seen: **cmdFunc 21** (main telemetry — SoC, packs, watts,
config) and **cmdFunc 22** (AC/voltage detail). Both are PARTIAL updates against
one big per-stream schema (each push carries only changed fields).

## SCHEMA SOLVED — DPU X = Delta Pro 3 protobuf dialect
tolwi/hassio-ecoflow-cloud already has a `delta_pro_ultra_x` device. The DPU X uses the **Delta Pro 3**
header + DisplayPropertyUpload field numbers, so the full field map is `ef_dp3_iobroker.proto`
(vendored in `reference/`). My `#1.9` header field is the **cmdId** (21 = DisplayPropertyUpload,
22 = a second stream); tolwi's outer cmdFunc for protobuf = 254. Reference files in `reference/`:
`ef_dp3_iobroker.proto` (full field map), `delta_pro_3.py` (decode pipeline), `delta_pro_ultra_x.py`
(entity/sensor set + the field-786 per-pack second pass), `ef_delta_pro_ultra_x.proto` (pack extra),
`diag_delta_pro_ultra_x.json` (a real NAMED snapshot — validates the decode).

Named keys we care about (from the DPU X module): headline `cms_batt_soc` (262), `pow_in_sum_w`,
`pow_out_sum_w`, `cms_chg_rem_time` (269), `cms_dsg_rem_time`, `cms_chg_dsg_state` (0 idle/1 dsg/2 chg);
AC input `plug_in_info_l1_vol`/`l1_amp`/`l2_vol`/`l2_amp` (69/151…); AC output `pow_get_ac`,
`pow_get_ac_hv_out` (240V), `pow_get_ac_lv_out` (120V), `pow_get_l1`/`l2`, `ac_out_freq`; solar
`pv_vin_ref`/`pv2_vin_ref`; per-pack `bp_<1..10>_soc`/`_temp` (field 786); limits `cms_max_chg_soc`/
`cms_min_dsg_soc`; temps `inv_ntc_temp2/3`,`llc_ntc_temp`,`temp_pcs_ac/dc`; plus firmware/errcodes.
No further Delta captures needed — schema is complete. Build a decoder from `ef_dp3_iobroker.proto`
(+ the 786 pack extra) and map these keys to Homey capabilities. tolwi's DPU X is READ-ONLY (control
suppressed) → v1 monitoring first; actuation (cmdId set messages) is a later phase.

## CONFIRMED by correlation
- **`262` fixed32 float = battery SoC (%)** — read 92.0 when the app showed 92% (2026-08-27).
- Idle (0 in / 0 out) capture = only tiny partial updates (mostly field `973` ≈ near-zero
  current/power vector). Wattage fields can't be told apart while idle → need a LOAD-ON capture.

## pdata (cmdFunc 21) — identified so far
- `120` fixed32 ≈ 125.4  and `1688` fixed32 ≈ 123.1 → **the two 120 V split-phase legs**
- `69` ≈ 125.0, `151` ≈ 122.6, `197` ≈ 120.0, `199` ≈ 125.0 (cmdFunc 22) → AC leg voltages
- `196` 0.54, `198` 1.47, `201` 0.29, `225` 1.79 → currents (A)?
- `459` = 12000 → **12 kW rating (DPU X)**; `460`=3600, `462`=12288 → config limits
- `293`=120000,`294`=2000,`295`=300000,`296`=60000 → charge/discharge limits (mW?)
- `102` = 33, `270` = 95, `271` = 10, `282` = 1 → candidates for SoC / flags (needs correlation)
- `134` = "America/New_York" (tz), `133` = -400 (tz offset min)
- `786` = **repeated per-battery-pack array**; each entry: `2`=id, `4`=SoC(91/93),
  `5`≈6.14, `6`≈6140 (pack mV?), `8`=temp(33/36)
- `976` = schedule entries (epoch + value)

## Status / next
Core is DE-RISKED: private API works, envelope + payload decode, key electrical
fields visible. Remaining: label each field → Homey capability. Best done by
CORRELATION — capture a probe dump while noting the EcoFlow app's readings
(battery %, input W, output W, each leg V, remaining time), plus optionally
cross-check community DP3/DPU protos (tolwi/hassio-ecoflow-cloud). Then build a
typed decoder (`ecoflow/proto.py`) + capability map, and the private-cloud client
(login + certification + MQTT, email/password stored per device).

Tools: `scripts/ecoflow_app_probe.py` (capture), `scripts/decode_proto.py`
(generic wire decoder), `ANALYSIS_decode.txt` (full dump of this capture).
