# Changelog

## v0.6.11

#### Changed
- **Aligned with Node-RED `dbus-victron-virtual` Virtual Switch** (Venus OS v3.80~13 beta):
  - Top-level `/State` path **removed** — Node-RED's official virtual switch does not
    expose it for switch devices, so neither do we.
  - `/SwitchableOutput/output_1/Status` now uses `0x01` (Powered, bit 0) for ON instead
    of the previous `0x09`. `0x00` for OFF unchanged. Matches the Victron
    `SwitchableOutput` bitmask definition.

#### Added
- **Text formatters** for previously unformatted dbus paths — labels now appear in
  GUI v2 / VRM the same way as Node-RED's virtual switch:
  - `…/Status` → `"On"` / `"Off"`
  - `…/Settings/Type` → `"Toggle"` / `"Dimmable"` / `"RGB"` / `"CCT"` / `"RGBW"`
  - `…/Settings/ShowUIControl` → `"All UIs"` / `"Local only"` / `"Remote only"`
- `/SwitchableOutput/output_1/State` formatter changed from `"ON"`/`"OFF"` to
  `"On"`/`"Off"` (capitalisation matches Node-RED).

#### Fixed
- **Reconnect ghost-state bug** (multi-layer defence):
  When the user clicked switches during the dead window (device unplugged but driver
  not yet aware, ~30 s before LWT/timeout fires), the cooldown started by those clicks
  could still be active when the device's `on_connect` retained state arrived,
  suppressing the real state and leaving the GUI stuck on the click state.
  Three independent safety nets now clear `last_cmd_time`:
  1. **MQTT broker (re)connect** — `on_connect` callback in the driver clears it
     before subscribing, so any stale cooldown from before the broker hiccup is gone.
  2. **`/Connected` 0 → 1 transition** — when `_update()` flips `/Connected` back to
     `1` after receiving the first state message, the cooldown is cleared at the same
     time, so the very first push to dbus is never silently swallowed.
  3. **LWT "online" payload** — already cleared in v0.6.10, retained as the third
     net for cases where the LWT message arrives before the state message.
- `_snap_to_offline()` now also resets `Dimming` to `0` and zeroes the brightness
  component of `LightControls`, so dimmer/RGB/CCT/RGBW sliders snap back to OFF
  alongside the on/off square when a ghost command is rejected (previously only
  `State` and `Status` were reverted, leaving the slider stuck at the click value).

## v0.6.10

#### Added
- **LWT / availability topic support** — device disappears from the Venus OS GUI when
  offline and reappears automatically when it comes back online.
  - New `[MQTT]` config keys (all optional):
    - `availability_topic` — topic the broker publishes to when device connects/disconnects
    - `payload_available` — payload meaning "online" (default: `online`)
    - `payload_unavailable` — payload meaning "offline" (default: `offline`)
  - Compatible with ESPHome (`<node>/status`), Shelly (`shellies/<id>/online`),
    Tasmota (`tele/<id>/LWT`), and any device following Home Assistant MQTT conventions.
  - **On offline (LWT unavailable payload)**: the GLib event loop is stopped, the process
    exits cleanly (after a 3 s delay to avoid daemontools spin), and the dbus service
    unregisters — the device disappears from the switch pane entirely.
  - **On restart after offline**: the process waits up to 15 s for any LWT message and,
    if the device is still offline, blocks before registering with dbus — device does not
    reappear in the GUI until it is actually reachable again.
  - **On reconnect (LWT available payload)**: stale cooldown cleared so the device's
    `on_connect` state publish (with `retain: true`) syncs the real state immediately.
  - Without `availability_topic`: falls back to timeout-based behaviour (see below).

#### Changed
- **Timeout now also exits the process** (not just sets `Connected = 0`). This makes the
  device disappear from the GUI on timeout, exactly like the LWT path. Daemontools restarts
  the driver and on the next start it waits for fresh data. Provides Cerbo-side detection
  that works independently of LWT and the broker.
- Default `timeout` lowered from `120` to `45` seconds in `config.sample.ini` (1.5× a
  typical 30 s state-publish interval).

#### Added
- `custom_name` and `group` documented as first-class config keys in `config.sample.ini`
  with a clear note explaining that GUI edits are runtime-only and the `.ini` is the
  single source of truth (same approach as the rest of mr-manuel's dbus-mqtt-* series).

## v0.6.9

#### Fixed
- On timeout (device unreachable), `SwitchableOutput/output_1/State` and `Status` are now
  reset to `0` / `0x00` (OFF). Previously the GUI kept showing the last known state (e.g. ON)
  even after the device was physically disconnected.
- **Ghost commands blocked**: GUI commands are now rejected when `/Connected = 0` (device
  offline). Previously, clicking switches while the device was unreachable published MQTT
  "ghost commands" and updated `last_cmd_time`, which caused the real device state to be
  suppressed on reconnect — and worse, the user could think they had toggled something
  (e.g. a bilge pump) when nothing was actually sent. The GUI value is now immediately
  reverted to OFF via `GLib.idle_add` so the switch snaps back, making it clear no command
  was sent.
- On MQTT reconnect after a silence longer than `CMD_COOLDOWN`, any stale cooldown left by
  ghost commands is cleared so the device's real state syncs immediately.

## v0.6.8

#### Fixed
- Command cooldown (`CMD_COOLDOWN = 3s`) now suppresses **all** MQTT feedback (state +
  dimming + color) after a GUI command, not just dimming/color.  Prevents the brief ON→OFF
  flicker caused by ESPHome reporting `state=1` during the 1 s fade-to-off transition.

## v0.6.7

#### Changed
- `CMD_COOLDOWN` raised to `3 s` (was `2 s`) — ESPHome 1 s transition + 2 s safety margin.

## v0.6.6

#### Added
- `CMD_COOLDOWN` (default 2 s): after a GUI→MQTT command, MQTT feedback for dimming and
  color is suppressed to prevent slider ping-pong during ESPHome transition animations.

## v0.6.5

#### Changed
- `default_transition_length: 1s` on all dimmable ESPHome lights (types 2, 11, 12, 13)
  so brightness/color changes animate smoothly instead of snapping.

## v0.6.4

#### Fixed
- `/CustomName` (breadcrumb) now uses device name ("Switch 2") instead of the type label,
  eliminating the duplicate "Virtual Dimmer: Virtual Dimmer" display. Breadcrumb and output
  label are now independent, matching Node-RED `dbus-victron-virtual` behaviour.

## v0.6.3

#### Changed
- `/State` always `0` — matches Node-RED `dbus-victron-virtual` (previously showed `0x100 Running`)
- `CustomName` for each output now derived from switch type by default:
  type 1→"Virtual Toggle", 2→"Virtual Dimmer", 11→"Virtual RGB", 12→"Virtual CCT", 13→"Virtual RGBW"
  (overridable via `custom_name =` in config)

## v0.6.2

#### Fixed
- `/SwitchableOutput/output_1/Name` now set to type label ("Toggle", "Dimmable", "RGB", "CCT", "RGBW")
  instead of the custom name — device row now shows "Dimmable: Virtual Dimmer" matching Node-RED format

## v0.6.1

#### Changed
- Device names renamed from "Light N" to "Switch N" in all sample config files
- `ProductName` = device name ("Switch 1" etc.) — device card title in the list
- `CustomName` = `"Virtual {device_name}"` by default — overridable via `custom_name =` in config
- Group default changed from `"Luces"` to `"Lights"`
- `custom_name` config key added (optional per-device override)

## v0.6.0

#### Changed
- `ProductId` → `0xC069` (Virtual switch — same as Node-RED `dbus-victron-virtual`)
- `Mgmt/ProcessName` → `"dbus-victron-virtual"` (matches Node-RED library identity)
- `SwitchableOutput` path renamed from `output_0/` → `output_1/` (Node-RED convention)
- `Settings/Function` and `Settings/ValidFunctions` removed (were IO Extender paths, not applicable)
- `Settings/Group` type changed from integer to string — devices with the same group string are grouped together in the switch pane

#### Added
- `ShowUIControl = 1` (All UIs: local + VRM) — required for VRM switch panel (pending Victron rollout)

#### Notes
- VRM switch panel confirmed not yet deployed to production VRM (Victron, April 2026)
- Driver is structurally ready for VRM when the panel goes live

## v0.5.0

#### Added
- Type `12` (CCT): on/off + brightness + color temperature slider (Kelvin)
- Type `13` (RGBW): on/off + brightness + color wheel + white channel
- Multi-instance support: one daemontools service per `config-{name}.ini` file
- `install.sh` auto-discovers all `config-*.ini` and creates services dynamically
- `uninstall.sh` removes all services and cleans `/data/rc.local`
- `restart.sh` restarts all running instances
- Persistence via `/data/rc.local` — driver survives Venus OS firmware updates
- Module state: shows `Running` (0x100) when MQTT data is received, `0` on timeout
- Configurable timeout: device marked disconnected if no MQTT within N seconds

#### Changed
- Config file naming: `config.ini` → `config-{name}.ini` (supports multiple instances)
- Config file is now a required argument: `dbus-mqtt-switch.py config-{name}.ini`
- Color temperature protocol (type 12): uses **Kelvin** — matches Venus OS GUI native scale
- Dimming for type 11 (RGB): separate `dimming` field in MQTT payload (was brightness-scaled in RGB values)

#### Notes
- Based on the [dbus-mqtt series by mr-manuel](https://github.com/mr-manuel)
- Tested on Venus OS v3.72 / Cerbo GX
