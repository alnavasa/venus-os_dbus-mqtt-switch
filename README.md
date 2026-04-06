# dbus-mqtt-switch — Venus OS driver

Creates `com.victronenergy.switch` devices on Venus OS from MQTT data.
Appears in the GUI v2 switch pane alongside Shelly devices and GX relays.
Supports **5 light/switch types** with full bidirectional control: Venus OS GUI ↔ MQTT ↔ device (ESP32/ESPHome or any MQTT client).



<img width="575" height="754" alt="Screenshot" src="https://github.com/user-attachments/assets/c186f41e-e303-4ee5-bd76-14c04f0b1bfd" />
<img width="1291" height="817" alt="Screenshot2" src="https://github.com/user-attachments/assets/32cfe3c2-f2b6-4ed7-8c77-d2285fa1d030" />


---

## Table of Contents

- [Supported types](#supported-types)
- [MQTT payload format](#mqtt-payload-format)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Multiple instances](#multiple-instances)
- [Update](#update)
- [Uninstall](#uninstall)
- [Restart](#restart)
- [Debugging](#debugging)
- [Compatibility](#compatibility)
- [Credits](#credits)

---

## Supported types

| Type | Name | GUI controls |
|------|------|-------------|
| `1` | Toggle | On / Off |
| `2` | Dimmable | On / Off + brightness slider (0–100 %) |
| `11` | RGB | On / Off + brightness + color wheel (HSV) |
| `12` | CCT | On / Off + brightness + color temperature slider (Kelvin) |
| `13` | RGBW | On / Off + brightness + color wheel + white channel |

---

## MQTT payload format

The driver subscribes to a **state topic** (device → Cerbo) and publishes to a **command topic** (Cerbo → device, default: `<topic>/set`).

All payloads are JSON.

<details>
<summary>Type 1 — Toggle</summary>

```json
{"state": 1}
{"state": 0}
```
</details>

<details>
<summary>Type 2 — Dimmable</summary>

```json
{"state": 1, "dimming": 75}
```

- `dimming`: brightness 0–100 %
</details>

<details>
<summary>Type 11 — RGB</summary>

```json
{"state": 1, "red": 255, "green": 128, "blue": 0, "dimming": 75}
```

- `red`, `green`, `blue`: 0–255 (pure color, not brightness-scaled)
- `dimming`: brightness 0–100 %
</details>

<details>
<summary>Type 12 — CCT</summary>

```json
{"state": 1, "colortemp": 2700, "dimming": 75}
```

- `colortemp`: color temperature in **Kelvin** (e.g. 2700 K warm white, 6500 K cool white)
- `dimming`: brightness 0–100 %
</details>

<details>
<summary>Type 13 — RGBW</summary>

```json
{"state": 1, "red": 255, "green": 128, "blue": 0, "white": 50, "dimming": 75}
```

- `red`, `green`, `blue`: 0–255
- `white`: white channel 0–100 %
- `dimming`: brightness 0–100 %
</details>

---

## Requirements

- Venus OS v3.00 or later (GUI v2 switch pane)
- Cerbo GX, Ekrano GX, or any Venus OS device
- MQTT broker reachable from the Cerbo (local broker on `127.0.0.1:1883` works)

---

## Installation

Connect to your Cerbo via SSH and run:

```bash
# 1. Download the driver
wget -q -O /tmp/dbus-mqtt-switch.zip \
  https://github.com/alnavasa/venus-os_dbus-mqtt-switch/archive/refs/heads/main.zip
unzip -q /tmp/dbus-mqtt-switch.zip -d /tmp/
cp -r /tmp/venus-os_dbus-mqtt-switch-main/dbus-mqtt-switch /data/etc/dbus-mqtt-switch

# 2. Create your config file from the sample
cd /data/etc/dbus-mqtt-switch
cp config.sample.ini config-mydevice.ini
# Edit config-mydevice.ini with your MQTT topic, device name, type, etc.
nano config-mydevice.ini

# 3. Install and start
bash install.sh
```

The installer creates a daemontools service for each `config-*.ini` file found and adds a hook to `/data/rc.local` so the driver survives Venus OS firmware updates.

---

## Configuration

Copy `config.sample.ini` to `config-{name}.ini` and edit it. Each file becomes a separate Venus OS device.

| Key | Section | Default | Description |
|-----|---------|---------|-------------|
| `logging` | DEFAULT | `WARNING` | Log level: `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `device_name` | DEFAULT | `MQTT Switch` | Name shown in Venus OS GUI |
| `device_instance` | DEFAULT | `200` | Unique device number (100–255, must be unique per instance) |
| `timeout` | DEFAULT | `120` | Seconds without MQTT → device marked disconnected. `0` = disabled |
| `type` | DEFAULT | `1` | Switch/light type: `1`, `2`, `11`, `12`, `13` |
| `broker_address` | MQTT | `127.0.0.1` | IP or hostname of MQTT broker |
| `broker_port` | MQTT | `1883` | MQTT broker port |
| `topic` | MQTT | *(required)* | State topic — driver subscribes here |
| `topic_command` | MQTT | `<topic>/set` | Command topic — driver publishes here |
| `username` | MQTT | *(empty)* | MQTT username (optional) |
| `password` | MQTT | *(empty)* | MQTT password (optional) |
| `tls_enabled` | MQTT | `0` | Enable TLS: `1` / `0` |
| `tls_path_to_ca` | MQTT | *(empty)* | Path to CA certificate |
| `tls_insecure` | MQTT | `0` | Skip certificate verification |

---

## Multiple instances

Each `config-{name}.ini` file becomes an independent Venus OS device with its own daemontools service.

```bash
# Example: three different lights
config-salon.ini      # device_instance = 200, type = 11 (RGB)
config-cockpit.ini    # device_instance = 201, type = 12 (CCT)
config-mast.ini       # device_instance = 202, type = 2  (Dimmable)
```

Run `install.sh` once after adding new config files — it auto-detects all `config-*.ini` and creates the missing services.

---

## Update

```bash
cd /data/etc/dbus-mqtt-switch

# Download new version (preserves your config-*.ini files)
wget -q -O /tmp/dbus-mqtt-switch.zip \
  https://github.com/alnavasa/venus-os_dbus-mqtt-switch/archive/refs/heads/main.zip
unzip -q -o /tmp/dbus-mqtt-switch.zip \
  "venus-os_dbus-mqtt-switch-main/dbus-mqtt-switch/dbus-mqtt-switch.py" \
  "venus-os_dbus-mqtt-switch-main/dbus-mqtt-switch/install.sh" \
  "venus-os_dbus-mqtt-switch-main/dbus-mqtt-switch/uninstall.sh" \
  "venus-os_dbus-mqtt-switch-main/dbus-mqtt-switch/restart.sh" \
  -d /tmp/
cp /tmp/venus-os_dbus-mqtt-switch-main/dbus-mqtt-switch/*.sh \
   /tmp/venus-os_dbus-mqtt-switch-main/dbus-mqtt-switch/*.py \
   /data/etc/dbus-mqtt-switch/

bash restart.sh
```

---

## Uninstall

```bash
bash /data/etc/dbus-mqtt-switch/uninstall.sh
# Optionally remove all driver files:
rm -rf /data/etc/dbus-mqtt-switch
```

---

## Restart

```bash
bash /data/etc/dbus-mqtt-switch/restart.sh
```

---

## Debugging

```bash
# Live log for a specific instance
tail -f /var/log/dbus-mqtt-switch-mydevice/current

# Check service status
svstat /service/dbus-mqtt-switch-*

# Enable verbose logging: set logging = DEBUG in config-*.ini, then restart
```

---

## Compatibility

Tested on:

| Venus OS | Cerbo GX |
|----------|----------|
| v3.72 | ✅ |
| v3.50+ | should work |

---

## Credits

Based on the [dbus-mqtt series by mr-manuel](https://github.com/mr-manuel).
Extended to support all 5 Venus OS switch/light types (toggle, dimmable, RGB, CCT, RGBW)
with full bidirectional MQTT control and multi-instance support.
