# EcoFlow Whole-Home — Homey app

A [Homey](https://homey.app) app (Python SDK, SDK3) for the EcoFlow **Smart Home
Panel 3** and **Delta Pro Ultra X** — the 2026 generation that the official
EcoFlow Homey app doesn't yet cover.

## Features

**Delta Pro Ultra X**
- Battery %, input / output / net power, charge status, time remaining
- Split-phase AC input voltages, AC output power, solar input, system temperature
- Per-battery-pack state of charge and temperature

**Smart Home Panel 3**
- Whole-panel grid / home-load / storage power, per-leg grid voltage
- A switch for each of the 32 circuits — on/off control + live power, named from
  your EcoFlow circuit labels (240V appliances merged into a single switch)
- "Grid power turned off / on" Flow triggers (grid-outage detection)

## How it works

The EcoFlow **developer** API blocks these device categories (error 1006), so the
app talks to the same private cloud the EcoFlow phone app uses: email/password →
token → MQTT. Device telemetry is **protobuf** (the Delta Pro 3 dialect), decoded
by a small dependency-free wire decoder in `ecoflow/` — no protobuf runtime to
cross-compile. Circuit on/off commands were reverse-engineered from the app's own
MQTT `set` messages and are verified byte-for-byte (`ecoflow/control.py`).

Credentials are **your own** and are stored per device; nothing is baked into the
package.

## Install

Requires the Homey CLI and Docker (for the Python dependency build).

```bash
homey app install
```

Pair each device with your EcoFlow app email/password and the device's serial
number (circuit switches auto-detect the panel's serial).

> **Windows note:** if `homey app validate`/`install` fails with
> *"Error while collecting cross-compiled virtual environment"*, run
> `bash scripts/fix_windows_venv.sh` and retry (dangling venv symlinks).

## Credits & disclaimer

Protocol knowledge and the Delta Pro 3 / Smart Home Panel 3 field maps are derived
from the excellent [tolwi/hassio-ecoflow-cloud](https://github.com/tolwi/hassio-ecoflow-cloud)
project. This is an **unofficial** app, not affiliated with or endorsed by EcoFlow;
it uses EcoFlow's private API, which may change at any time.
