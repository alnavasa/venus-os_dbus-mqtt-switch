# Changelog

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
