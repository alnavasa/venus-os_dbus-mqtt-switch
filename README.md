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
- [What's new](#whats-new)
- [Configuration](#configuration)
  - [Persistence of names and groups](#persistence-of-names-and-groups)
  - [Availability / LWT](#availability--lwt)
  - [Advanced tuning](#advanced-tuning)
- [Multiple instances](#multiple-instances)
- [Update](#update)
- [Uninstall](#uninstall)
- [Restart](#restart)
- [Debugging](#debugging)
- [Compatibility](#compatibility)
- [Credits](#credits)

---

## What's new

### v0.6.13 — fast-cycle reconnect fix + tunable timings *(latest)*

- Fixes the last remaining reconnect edge case: unplug → touch sliders → plug back in within ~10 s no longer leaves the GUI showing stale slider values until the next state change.
- **Three new defensive layers** on top of v0.6.12: cooldown bypass on recent reconnect, re-subscribe polling at +2 s / +5 s / +10 s after any reconnect, and faster process restart after LWT offline (1 s vs. 3 s).
- **All timing constants are now configurable** via optional keys in `[DEFAULT]` — `cmd_cooldown`, `reconnect_bypass`, `resubscribe_poll`, `lwt_wait_timeout`, `exit_sleep`. Defaults unchanged, backward-compatible. See [Advanced tuning](#advanced-tuning) for when and how to tune them.

### v0.6.12 — ghost dimmer value on reconnect

- Fixes the "ghost dimmer value" bug where, after a reconnect, the GUI would show the slider value the user touched during the dead window instead of the device's real state.
- Introduces a `fresh_state` gate that prevents registering on dbus with stale retained state from before the device went offline.
- Wipes all cached value globals on LWT offline so nothing can leak across a disappear/reappear cycle.

### v0.6.11 — Node-RED Virtual Switch alignment

- Dropped top-level `/State`, switched `Status` to `0x01`/`0x00` (Node-RED convention).
- Added text formatters so GUI v2 / VRM show human-readable labels ("On"/"Off", "Dimmable", "All UIs", etc.).
- Multi-layer defence against ghost commands during the reconnect dead window.

### v0.6.10 — LWT / availability topic support

- Device now disappears from the Venus OS GUI instantly when offline (via LWT) and reappears automatically when reachable again.
- Compatible with ESPHome, Shelly, Tasmota and Home Assistant MQTT conventions.

> 📖 **See the full [`CHANGELOG.md`](CHANGELOG.md)** for every release since v0.5.0, including all bug fixes and internal changes.

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
| `device_name` | DEFAULT | `MQTT Switch` | ProductName + breadcrumb name in Venus OS GUI |
| `custom_name` | DEFAULT | type-based | Output label inside the switch card (e.g. "Cabin Light") |
| `group` | DEFAULT | `Lights` | Group label in the GUI v2 switch pane (switches sharing the same group are grouped together) |
| `device_instance` | DEFAULT | `200` | Unique device number (100–255, must be unique per instance) |
| `timeout` | DEFAULT | `45` | Seconds without MQTT → device disappears from GUI. `0` = disabled |
| `type` | DEFAULT | `1` | Switch/light type: `1`, `2`, `11`, `12`, `13` |
| `broker_address` | MQTT | `127.0.0.1` | IP or hostname of MQTT broker |
| `broker_port` | MQTT | `1883` | MQTT broker port |
| `topic` | MQTT | *(required)* | State topic — driver subscribes here |
| `topic_command` | MQTT | `<topic>/set` | Command topic — driver publishes here |
| `availability_topic` | MQTT | *(empty)* | LWT topic for instant offline detection (see [Availability / LWT](#availability--lwt)) |
| `payload_available` | MQTT | `online` | LWT payload meaning "device online" |
| `payload_unavailable` | MQTT | `offline` | LWT payload meaning "device offline" |
| `username` | MQTT | *(empty)* | MQTT username (optional) |
| `password` | MQTT | *(empty)* | MQTT password (optional) |
| `tls_enabled` | MQTT | `0` | Enable TLS: `1` / `0` |
| `tls_path_to_ca` | MQTT | *(empty)* | Path to CA certificate |
| `tls_insecure` | MQTT | `0` | Skip certificate verification |
| `cmd_cooldown` | DEFAULT | `3` | Seconds to suppress MQTT feedback after a GUI command (anti-ping-pong) — see [Advanced tuning](#advanced-tuning) |
| `reconnect_bypass` | DEFAULT | `5` | Seconds after a reconnect during which the cooldown is bypassed — see [Advanced tuning](#advanced-tuning) |
| `resubscribe_poll` | DEFAULT | `2,5,10` | Seconds at which to re-subscribe after a reconnect (forces retained redelivery) — see [Advanced tuning](#advanced-tuning) |
| `lwt_wait_timeout` | DEFAULT | `15` | Seconds to wait for first LWT message at startup — see [Advanced tuning](#advanced-tuning) |
| `exit_sleep` | DEFAULT | `1` | Seconds before `sys.exit(0)` after LWT offline / timeout — see [Advanced tuning](#advanced-tuning) |

### Persistence of names and groups

`device_name`, `custom_name` and `group` are read from `config-{name}.ini` **every time the driver starts**.

You can edit them from the Venus OS GUI v2 switch pane (right-click → Edit), but those changes are **runtime-only** — they will be reset when the service restarts (e.g. when the physical device disconnects/reconnects, when the Cerbo reboots, or after a firmware update).

To rename a switch or move it to a different group **permanently**:

1. Edit the corresponding `config-{name}.ini` (`device_name` / `custom_name` / `group`)
2. Restart the service:
   ```bash
   bash /data/etc/dbus-mqtt-switch/restart.sh
   ```

This is the same approach used by the rest of the [`dbus-mqtt-*` series by mr-manuel](https://github.com/mr-manuel) and avoids touching Victron's `localsettings` database. The config file is the single source of truth.

### Availability / LWT

Setting `availability_topic` enables **instant offline detection** using the standard MQTT LWT (Last Will and Testament) mechanism. It is the same pattern used by Shelly, Tasmota, ESPHome and Home Assistant MQTT devices.

When the broker stops receiving keepalive pings from the physical device, it publishes the LWT "unavailable" payload. The driver receives it, exits the GLib event loop and the dbus service unregisters — **the device disappears from the GUI**.

When the device comes back online and publishes its first message (or the broker publishes the "available" payload), the driver re-registers and the device **reappears with its real, current state** (assuming the device publishes its state with `retain: true` so the driver gets it on reconnect).

| Implementation | `availability_topic` example | `payload_available` | `payload_unavailable` |
|---|---|---|---|
| ESPHome (default) | `<node_name>/status` | `online` | `offline` |
| Shelly | `shellies/<device-id>/online` | `true` | `false` |
| Tasmota | `tele/<device-id>/LWT` | `Online` | `Offline` |

If you don't set `availability_topic`, the driver falls back to **timeout-based detection** using the `timeout` key — the device disappears from the GUI after `timeout` seconds with no MQTT message. The recommended `timeout = 45` works well as a backup or as the only mechanism when LWT isn't available.

For ESPHome devices, set `keepalive: 15s` in the `mqtt:` section of your YAML so the broker detects the disconnect quickly (~22 s).

### Advanced tuning

All of the timing behaviour can be tuned via optional keys in the `[DEFAULT]` section of `config-{name}.ini`. The defaults work for ESPHome devices with `default_transition_length: 1s` and `keepalive: 15s`, but you can raise or lower them for unusual brokers, slow devices, or long transition animations.

| Key | Default | What it controls | When to raise |
|-----|---------|------------------|---------------|
| `cmd_cooldown` | `3` | Seconds to ignore MQTT feedback after a GUI command. Prevents the slider from ping-ponging back to an intermediate value while the device runs its transition animation. | If you use ESPHome `default_transition_length > 1 s`, set `cmd_cooldown = transition_length + 2`. |
| `reconnect_bypass` | `5` | Seconds after a reconnect event (LWT online or `/Connected` 0 → 1) during which the command cooldown is bypassed. Fixes the fast-cycle case: user moves slider → unplugs device → plugs back in within seconds → device boots with its own defaults. The post-reconnect state publish is the truth and must not be suppressed. | If your device takes longer than ~5 s to publish retained state after booting. |
| `resubscribe_poll` | `2,5,10` | Comma-separated list of seconds at which to re-subscribe to the state topic after a reconnect event. Each re-subscribe forces the broker to redeliver the retained state on that topic, catching any post-reconnect publish that arrived after the driver's initial subscribe. Set to an empty string to disable polling. | Use a more aggressive list (`1,3,5,10,20`) on very slow devices or unreliable brokers. |
| `lwt_wait_timeout` | `15` | Seconds to wait at startup for the first LWT (availability) message before assuming the device is online and proceeding with dbus registration. Only used when `availability_topic` is set. | Raise on unusual brokers that delay retained message delivery. |
| `exit_sleep` | `1` | Seconds to sleep before `sys.exit(0)` after the device goes offline (LWT unavailable or timeout). Kept short because the LWT wait loop in the next process start blocks daemontools from spinning. | Raise only if your daemontools is unhappy with very fast restarts. |

**Tight recommendation for most setups:** leave these at their defaults. The values were chosen to handle the three realistic reconnect scenarios (slow cycle, normal cycle, < 10 s fast cycle) robustly with ESPHome devices at `transition_length: 1s` and `keepalive: 15s`.

**Relationship to ESPHome keepalive:** the MQTT broker detects a dead device after roughly `1.5 × keepalive` seconds. With `keepalive: 15s` that is ~22 s. If you raise the ESPHome keepalive, consider raising the `timeout` key (safety-net offline detection) proportionally.

Example `config-{name}.ini` with all tuning keys (all values at default — shown only to illustrate where they go):

```ini
[DEFAULT]
device_name     = Cabin Light
device_instance = 201
timeout         = 45
type            = 2

# Advanced tuning — all optional, defaults shown:
cmd_cooldown      = 3
reconnect_bypass  = 5
resubscribe_poll  = 2,5,10
lwt_wait_timeout  = 15
exit_sleep        = 1

[MQTT]
broker_address     = 127.0.0.1
broker_port        = 1883
topic              = home/cabin/light
availability_topic = home/cabin/status
```

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
