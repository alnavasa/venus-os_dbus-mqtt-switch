# Changelog

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
