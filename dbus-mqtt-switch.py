#!/usr/bin/env python3
"""
dbus-mqtt-switch  —  Venus OS driver v0.6.0
===========================================
Creates a com.victronenergy.switch device from MQTT data.
Appears in Venus OS GUI v2 switch pane alongside Shelly, GX relays, etc.

Supported types
---------------
  1  = toggle (simple on/off)
  2  = dimmable (on/off + brightness slider 0-100%)
  11 = RGB light (on/off + brightness + color RGB)
  12 = CCT light (on/off + brightness + color temperature)
  13 = RGBW light (on/off + brightness + color RGB + white channel)

MQTT topics
-----------
State topic   (device → Cerbo):   configured as `topic` in config.ini
  Payload type 1:  {"state": 1}
  Payload type 2:  {"state": 1, "dimming": 75}
  Payload type 11: {"state": 1, "red": 255, "green": 0, "blue": 0, "dimming": 50}
  Payload type 12: {"state": 1, "colortemp": 2700, "dimming": 75}  ← colortemp in Kelvin
  Payload type 13: {"state": 1, "red": 255, "green": 0, "blue": 0, "white": 80, "dimming": 50}

Command topic (Cerbo → device):   default: topic + "/set"
  Same payload format — published when user changes value in Venus OS GUI.

Multiple instances
------------------
  python3 dbus-mqtt-switch.py config-light1.ini  # instance "light1"
  python3 dbus-mqtt-switch.py config-light2.ini  # instance "light2"
  Each config-{name}.ini becomes a separate Venus OS device.
  Run install.sh to auto-create services for all config files.

Based on mr-manuel's dbus-mqtt-* pattern.
https://github.com/mr-manuel
"""

from gi.repository import GLib  # pyright: ignore[reportMissingImports]
import platform
import logging
import sys
import os
from time import sleep, time
import json
import colorsys
import configparser
import _thread

# external packages (bundled in ext/)
sys.path.insert(1, os.path.join(os.path.dirname(__file__), "ext"))
import paho.mqtt.client as mqtt

# Victron Energy packages (bundled in ext/velib_python/)
sys.path.insert(1, os.path.join(os.path.dirname(__file__), "ext", "velib_python"))
from vedbus import VeDbusService          # noqa: E402
from ve_utils import get_vrm_portal_id    # noqa: E402

VERSION = "0.6.1"


# ── HSV ↔ RGB conversion (for Venus OS GUI v2 color wheel) ────────────────────

def rgb_to_hsv(r, g, b):
    """RGB (0-255) → HSV (H:0-360, S:0-100, V:0-100)"""
    h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    return round(h * 360, 1), round(s * 100, 1), round(v * 100, 1)


def hsv_to_rgb(h, s, v):
    """HSV (H:0-360, S:0-100, V:0-100) → RGB (0-255)"""
    r, g, b = colorsys.hsv_to_rgb(h / 360.0, s / 100.0, v / 100.0)
    return int(round(r * 255)), int(round(g * 255)), int(round(b * 255))


# ── Config ─────────────────────────────────────────────────────────────────────

try:
    # Config file is required as command line argument (set by service/run)
    if len(sys.argv) > 1:
        config_file = sys.argv[1]
        if not os.path.isabs(config_file):
            config_file = os.path.join(os.path.dirname(os.path.realpath(__file__)), config_file)
    else:
        print('ERROR: config file argument required.')
        print('Usage: dbus-mqtt-switch.py config-{name}.ini')
        print('Run install.sh to set up services automatically.')
        sleep(60)
        sys.exit()

    if os.path.exists(config_file):
        config = configparser.ConfigParser()
        config.read(config_file)
    else:
        print(f'ERROR: "{config_file}" not found.')
        print('Copy config.sample.ini to config-{name}.ini and edit. Then run install.sh.')
        sleep(60)
        sys.exit()
except Exception:
    exc_type, exc_obj, exc_tb = sys.exc_info()
    print(f"Exception: {repr(exc_obj)} in {exc_tb.tb_frame.f_code.co_filename}:{exc_tb.tb_lineno}")
    sleep(60)
    sys.exit()


# ── Logging ────────────────────────────────────────────────────────────────────

level_map = {"DEBUG": logging.DEBUG, "INFO": logging.INFO,
             "WARNING": logging.WARNING, "ERROR": logging.ERROR}
logging.basicConfig(level=level_map.get(
    config.get("DEFAULT", "logging", fallback="WARNING"), logging.WARNING))


# ── Config values ──────────────────────────────────────────────────────────────

timeout         = int(config.get("DEFAULT", "timeout",         fallback="120"))
switch_type     = int(config.get("DEFAULT", "type",            fallback="1"))
device_name     = config.get("DEFAULT", "device_name",         fallback="Light")
device_group    = config.get("DEFAULT", "group",               fallback="Lights")
device_instance = int(config.get("DEFAULT", "device_instance", fallback="200"))

# ProductName = device_name (e.g. "Switch 1") — shown in device list and VRM
product_name = device_name
# CustomName = output label shown inside the switch card (e.g. "Virtual Switch 1")
custom_name  = config.get("DEFAULT", "custom_name", fallback=f"Virtual {device_name}")

try:
    topic_state = config.get("MQTT", "topic")
except Exception:
    print('ERROR: "topic" is missing from [MQTT] section in config.ini. Restarting in 60s.')
    sleep(60)
    sys.exit()
topic_command = config.get("MQTT", "topic_command", fallback=topic_state + "/set")

logging.info(f"dbus-mqtt-switch v{VERSION} — config: {config_file}")
logging.info(f"  device='{device_name}' instance={device_instance} type={switch_type}")
logging.info(f"  topics: state='{topic_state}'  command='{topic_command}'")


# ── State variables ────────────────────────────────────────────────────────────

connected    = 0
last_changed = 0
last_updated = 0
state        = None    # int: 0 or 1
dimming      = None    # float: 0.0–100.0 (types 2, 11, 12, 13)
red          = None    # int: 0–255 (types 11, 13)
green        = None    # int: 0–255 (types 11, 13)
blue         = None    # int: 0–255 (types 11, 13)
colortemp    = None    # float: color temperature (type 12)
white        = None    # float: 0.0–100.0 white channel (type 13)
mqtt_client  = None    # set in main(), referenced by dbus callback


# ── MQTT callbacks ─────────────────────────────────────────────────────────────

def on_disconnect(client, userdata, flags, reason_code, properties):
    global connected
    logging.warning("MQTT: disconnected")
    if reason_code != 0:
        logging.warning("MQTT: unexpected disconnection — will reconnect")
    connected = 0
    while connected == 0:
        try:
            client.connect(
                host=config["MQTT"]["broker_address"],
                port=int(config["MQTT"]["broker_port"]))
            connected = 1
        except Exception as e:
            logging.error(f"MQTT: reconnect failed: {e}. Retrying in 15s")
            sleep(15)


def on_connect(client, userdata, flags, reason_code, properties):
    global connected
    if reason_code == 0:
        logging.info("MQTT: connected to broker")
        connected = 1
        client.subscribe(topic_state)
        logging.info(f"MQTT: subscribed to '{topic_state}'")
    else:
        logging.error(f"MQTT: connection failed, rc={reason_code}")


def on_message(client, userdata, msg):
    global last_changed, state, dimming, red, green, blue, colortemp, white
    try:
        if msg.topic != topic_state or not msg.payload:
            return

        payload = json.loads(msg.payload)

        if "state" in payload:
            state = int(payload["state"])

        if switch_type in (2, 11, 12, 13) and "dimming" in payload:
            dimming = float(payload["dimming"])
            dimming = max(0.0, min(100.0, dimming))

        if switch_type in (11, 13):
            if "red" in payload:
                red = max(0, min(255, int(payload["red"])))
            if "green" in payload:
                green = max(0, min(255, int(payload["green"])))
            if "blue" in payload:
                blue = max(0, min(255, int(payload["blue"])))

        if switch_type == 12 and "colortemp" in payload:
            colortemp = float(payload["colortemp"])

        if switch_type == 13 and "white" in payload:
            white = float(payload["white"])
            white = max(0.0, min(100.0, white))

        last_changed = int(time())
        logging.debug(f"MQTT rx: state={state} dim={dimming} rgb=({red},{green},{blue}) ct={colortemp} w={white}")

    except (json.JSONDecodeError, ValueError) as e:
        logging.error(f"MQTT: invalid payload: {e} — {msg.payload}")
    except Exception as e:
        logging.error(f"MQTT: on_message exception: {e}")


# ── dbus service ───────────────────────────────────────────────────────────────

class DbusMqttSwitchService:

    def __init__(self, servicename, deviceinstance, paths, productname, customname):
        self._dbusservice = VeDbusService(servicename, register=False)
        self._paths = paths

        logging.debug(f"{servicename} /DeviceInstance = {deviceinstance}")

        # Mandatory management paths — matching Node-RED dbus-victron-virtual format
        self._dbusservice.add_path("/Mgmt/ProcessName",    "dbus-victron-virtual")
        self._dbusservice.add_path("/Mgmt/ProcessVersion",
            f"v{VERSION} / Python {platform.python_version()}")
        self._dbusservice.add_path("/Mgmt/Connection",     "MQTT")
        self._dbusservice.add_path("/DeviceInstance",      deviceinstance)
        self._dbusservice.add_path("/ProductId",           0xC069)  # Virtual switch
        self._dbusservice.add_path("/ProductName",         productname)
        self._dbusservice.add_path("/CustomName",          customname)
        self._dbusservice.add_path("/Serial",              f"mqtt_{deviceinstance}")
        self._dbusservice.add_path("/Connected",           1)

        # Module-level state (0x100=Running, 0=disconnected) — used for timeout detection
        self._dbusservice.add_path("/State", 0x100)

        # SwitchableOutput channel — path naming matches Node-RED virtual switch (output_1)
        self._dbusservice.add_path("/SwitchableOutput/output_1/Name", customname)
        # Status = read-only hardware output state (0x00=Off, 0x09=On)
        self._dbusservice.add_path("/SwitchableOutput/output_1/Status", 0x00)

        # Settings — GUI v2 + VRM switch pane requires these to show the device
        # ShowUIControl bitmask: 1=All UIs (local+VRM), 2=Local only, 4=Remote only
        self._dbusservice.add_path(
            "/SwitchableOutput/output_1/Settings/ShowUIControl", 1,
            writeable=True, onchangecallback=self._handlechangedvalue)
        self._dbusservice.add_path(
            "/SwitchableOutput/output_1/Settings/CustomName", customname,
            writeable=True, onchangecallback=self._handlechangedvalue)
        self._dbusservice.add_path(
            "/SwitchableOutput/output_1/Settings/Group", device_group,
            writeable=True, onchangecallback=self._handlechangedvalue)
        # ValidTypes bitmask: bit N = type N is valid for this device
        # type 1 (toggle) → 1<<1 = 2, type 2 (dimmable) → 1<<2 = 4,
        # type 11 (RGB) → 1<<11 = 2048
        self._dbusservice.add_path(
            "/SwitchableOutput/output_1/Settings/ValidTypes", 1 << switch_type)

        # SwitchableOutput type — tells Venus OS how to render this output:
        #   1 = toggle, 2 = dimmable, 11 = RGB, 12 = CCT, 13 = RGBW
        self._dbusservice.add_path(
            "/SwitchableOutput/output_1/Settings/Type",
            switch_type,
            writeable=True,
            onchangecallback=self._handlechangedvalue)

        # Data paths (State, Dimming, RGB, UpdateIndex)
        for path, settings in self._paths.items():
            self._dbusservice.add_path(
                path,
                settings["initial"],
                gettextcallback=settings["textformat"],
                writeable=True,
                onchangecallback=self._handlechangedvalue)

        self._dbusservice.register()
        GLib.timeout_add(1000, self._update)

    def _update(self):
        """Called every second — push latest MQTT state to dbus."""
        global last_changed, last_updated
        now = int(time())

        if last_changed != last_updated:
            if state is not None:
                self._dbusservice["/SwitchableOutput/output_1/State"] = state
                self._dbusservice["/SwitchableOutput/output_1/Status"] = 0x09 if state else 0x00
            if dimming is not None and switch_type == 2:
                self._dbusservice["/SwitchableOutput/output_1/Dimming"] = dimming
            if switch_type == 11:
                if red is not None and green is not None and blue is not None:
                    # RGB from MQTT = pure color; dimming = brightness (separate)
                    h, s, _ = rgb_to_hsv(red, green, blue)
                    dim_val = dimming if dimming is not None else 100.0
                    self._dbusservice["/SwitchableOutput/output_1/LightControls"] = [
                        h, s, dim_val, 0.0, 0.0]
                    self._dbusservice["/SwitchableOutput/output_1/Dimming"] = dim_val

            if switch_type == 12:
                dim_val = dimming if dimming is not None else 100.0
                ct_val = colortemp if colortemp is not None else 2700.0
                self._dbusservice["/SwitchableOutput/output_1/LightControls"] = [
                    0.0, 0.0, dim_val, 0.0, ct_val]
                self._dbusservice["/SwitchableOutput/output_1/Dimming"] = dim_val

            if switch_type == 13:
                if red is not None and green is not None and blue is not None:
                    h, s, _ = rgb_to_hsv(red, green, blue)
                    dim_val = dimming if dimming is not None else 100.0
                    w_val = white if white is not None else 0.0
                    self._dbusservice["/SwitchableOutput/output_1/LightControls"] = [
                        h, s, dim_val, w_val, 0.0]
                    self._dbusservice["/SwitchableOutput/output_1/Dimming"] = dim_val

            # Mark as connected when receiving MQTT data
            if self._dbusservice["/Connected"] != 1:
                self._dbusservice["/Connected"] = 1
                self._dbusservice["/State"] = 0x100  # Running
                logging.info("Device connected (MQTT data received)")

            index = (self._dbusservice["/UpdateIndex"] + 1) % 256
            self._dbusservice["/UpdateIndex"] = index

            logging.debug(f"dbus updated: state={state} dimming={dimming} rgb=({red},{green},{blue}) ct={colortemp} w={white}")
            last_updated = last_changed

        # Timeout: mark as disconnected if no MQTT message within timeout seconds
        # Device stays in GUI but shows as disconnected (like mr-manuel drivers)
        # (set timeout=0 in config.ini to disable)
        if timeout != 0 and last_changed != 0 and (now - last_changed) > timeout:
            if self._dbusservice["/Connected"] != 0:
                self._dbusservice["/Connected"] = 0
                self._dbusservice["/State"] = 0  # Not connected
                logging.warning(
                    f"Timeout of {timeout}s exceeded — marking device disconnected.")

        return True

    def _handlechangedvalue(self, path, value):
        """
        Called when Venus OS GUI writes a value (user toggles switch, moves slider,
        or changes RGB).  Publishes the command to the device via MQTT.
        """
        global mqtt_client, state, dimming, red, green, blue, colortemp, white
        try:
            if path == "/SwitchableOutput/output_1/State":
                state = int(value)
                self._dbusservice["/SwitchableOutput/output_1/Status"] = 0x09 if state else 0x00
                payload = {"state": state}
                if switch_type == 2:
                    payload["dimming"] = dimming if dimming is not None else 100.0
                if switch_type == 11:
                    payload["red"]   = red   if red   is not None else 255
                    payload["green"] = green if green is not None else 255
                    payload["blue"]  = blue  if blue  is not None else 255
                    payload["dimming"] = dimming if dimming is not None else 100.0
                if switch_type == 12:
                    payload["colortemp"] = colortemp if colortemp is not None else 2700.0
                    payload["dimming"] = dimming if dimming is not None else 100.0
                if switch_type == 13:
                    payload["red"]   = red   if red   is not None else 255
                    payload["green"] = green if green is not None else 255
                    payload["blue"]  = blue  if blue  is not None else 255
                    payload["white"] = white if white is not None else 0.0
                    payload["dimming"] = dimming if dimming is not None else 100.0
                mqtt_client.publish(topic_command, json.dumps(payload))
                logging.info(f"GUI→MQTT [{topic_command}]: {payload}")

            elif path == "/SwitchableOutput/output_1/Dimming":
                dimming = float(value)
                if switch_type == 2:
                    payload = {"state": 0 if dimming == 0 else 1, "dimming": dimming}
                elif switch_type == 11:
                    # Brightness change — keep pure color RGB, send dimming separately
                    h, s, _ = rgb_to_hsv(
                        red if red is not None else 255,
                        green if green is not None else 255,
                        blue if blue is not None else 255)
                    self._dbusservice["/SwitchableOutput/output_1/LightControls"] = [
                        h, s, dimming, 0.0, 0.0]
                    payload = {
                        "state": 0 if dimming == 0 else 1,
                        "red":   red   if red   is not None else 255,
                        "green": green if green is not None else 255,
                        "blue":  blue  if blue  is not None else 255,
                        "dimming": dimming,
                    }
                elif switch_type == 12:
                    ct_val = colortemp if colortemp is not None else 2700.0
                    self._dbusservice["/SwitchableOutput/output_1/LightControls"] = [
                        0.0, 0.0, dimming, 0.0, ct_val]
                    payload = {
                        "state": 0 if dimming == 0 else 1,
                        "colortemp": ct_val,
                        "dimming": dimming,
                    }
                elif switch_type == 13:
                    h, s, _ = rgb_to_hsv(
                        red if red is not None else 255,
                        green if green is not None else 255,
                        blue if blue is not None else 255)
                    w_val = white if white is not None else 0.0
                    self._dbusservice["/SwitchableOutput/output_1/LightControls"] = [
                        h, s, dimming, w_val, 0.0]
                    payload = {
                        "state": 0 if dimming == 0 else 1,
                        "red":   red   if red   is not None else 255,
                        "green": green if green is not None else 255,
                        "blue":  blue  if blue  is not None else 255,
                        "white": w_val,
                        "dimming": dimming,
                    }
                else:
                    return True
                mqtt_client.publish(topic_command, json.dumps(payload))
                logging.info(f"GUI→MQTT [{topic_command}]: {payload}")

            elif path == "/SwitchableOutput/output_1/LightControls":
                # Venus OS GUI v2 writes [Hue, Sat, Brightness, White, ColorTemp]
                h  = float(value[0])
                s  = float(value[1])
                v  = float(value[2])
                w  = float(value[3])
                ct = float(value[4])

                if switch_type == 11:
                    red, green, blue = hsv_to_rgb(h, s, 100.0)
                    dimming = v
                    self._dbusservice["/SwitchableOutput/output_1/Dimming"] = v
                    payload = {
                        "state": state if state is not None else 1,
                        "red": red, "green": green, "blue": blue,
                        "dimming": dimming,
                    }
                elif switch_type == 12:
                    colortemp = ct
                    dimming = v
                    self._dbusservice["/SwitchableOutput/output_1/Dimming"] = v
                    payload = {
                        "state": state if state is not None else 1,
                        "colortemp": colortemp,
                        "dimming": dimming,
                    }
                elif switch_type == 13:
                    red, green, blue = hsv_to_rgb(h, s, 100.0)
                    dimming = v
                    white = w
                    self._dbusservice["/SwitchableOutput/output_1/Dimming"] = v
                    payload = {
                        "state": state if state is not None else 1,
                        "red": red, "green": green, "blue": blue,
                        "white": white,
                        "dimming": dimming,
                    }
                else:
                    return True
                mqtt_client.publish(topic_command, json.dumps(payload))
                logging.info(f"GUI→MQTT [{topic_command}]: {payload}")

            elif path == "/SwitchableOutput/output_1/Settings/Type":
                logging.info(f"Type changed to {value} via GUI (requires driver restart)")

        except Exception as e:
            logging.error(f"_handlechangedvalue error: {e}")
        return True  # accept the change


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    global mqtt_client
    _thread.daemon = True

    from dbus.mainloop.glib import DBusGMainLoop  # pyright: ignore[reportMissingImports]
    DBusGMainLoop(set_as_default=True)

    # MQTT client setup
    client_id = f"MqttSwitch_{get_vrm_portal_id()}_{device_instance}"
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id)
    client.on_disconnect = on_disconnect
    client.on_connect    = on_connect
    client.on_message    = on_message

    # TLS (optional)
    if config.get("MQTT", "tls_enabled", fallback="0") == "1":
        ca = config.get("MQTT", "tls_path_to_ca", fallback="")
        client.tls_set(ca if ca else None, tls_version=2)
        if config.get("MQTT", "tls_insecure", fallback=""):
            client.tls_insecure_set(True)

    # Auth (optional)
    username = config.get("MQTT", "username", fallback="")
    password = config.get("MQTT", "password", fallback="")
    if username and password:
        client.username_pw_set(username=username, password=password)

    # Connect
    broker = config["MQTT"]["broker_address"]
    port   = int(config["MQTT"]["broker_port"])
    logging.info(f"MQTT: connecting to {broker}:{port}")
    client.connect(host=broker, port=port)
    client.loop_start()
    mqtt_client = client

    # Wait for first state message before registering with dbus
    logging.info(f"Waiting for first MQTT message on '{topic_state}'...")
    i = 0
    while state is None:
        if timeout != 0 and timeout <= (i * 2):
            logging.warning("No initial state received — starting with state=0")
            break
        sleep(2)
        i += 1

    # Text formatters for dbus paths
    def _state_fmt(p, v): return "ON" if v else "OFF" if v is not None else "--"
    def _pct(p, v):       return f"{v:.0f}%" if v is not None else "--%"
    def _n(p, v):         return str(int(v)) if v is not None else "0"

    paths_dbus = {
        "/SwitchableOutput/output_1/State": {
            "initial":    state if state is not None else 0,
            "textformat": _state_fmt,
        },
        "/UpdateIndex": {
            "initial":    0,
            "textformat": _n,
        },
    }

    # Dimming path for type 2 (dimmable) and type 11 (RGB brightness)
    if switch_type in (2, 11, 12, 13):
        paths_dbus["/SwitchableOutput/output_1/Dimming"] = {
            "initial":    dimming if dimming is not None else 0.0,
            "textformat": _pct,
        }

    # LightControls array [Hue, Saturation, Brightness, White, ColorTemp]
    # Venus OS GUI v2 color wheel / sliders read/write this array
    if switch_type == 11:
        _h, _s, _v = 0.0, 0.0, 0.0
        if red is not None and green is not None and blue is not None:
            _h, _s, _ = rgb_to_hsv(red, green, blue)
            _v = dimming if dimming is not None else 100.0
        paths_dbus["/SwitchableOutput/output_1/LightControls"] = {
            "initial":    [_h, _s, _v, 0.0, 0.0],
            "textformat": lambda p, v: str(v),
        }

    if switch_type == 12:
        _v = dimming if dimming is not None else 0.0
        _ct = colortemp if colortemp is not None else 2700.0
        paths_dbus["/SwitchableOutput/output_1/LightControls"] = {
            "initial":    [0.0, 0.0, _v, 0.0, _ct],
            "textformat": lambda p, v: str(v),
        }

    if switch_type == 13:
        _h, _s, _v = 0.0, 0.0, 0.0
        if red is not None and green is not None and blue is not None:
            _h, _s, _ = rgb_to_hsv(red, green, blue)
            _v = dimming if dimming is not None else 100.0
        _w = white if white is not None else 0.0
        paths_dbus["/SwitchableOutput/output_1/LightControls"] = {
            "initial":    [_h, _s, _v, _w, 0.0],
            "textformat": lambda p, v: str(v),
        }

    DbusMqttSwitchService(
        servicename  = f"com.victronenergy.switch.mqtt_switch_{device_instance}",
        deviceinstance = device_instance,
        productname  = product_name,
        customname   = custom_name,
        paths        = paths_dbus,
    )

    logging.info("Registered on dbus — running event loop")
    GLib.MainLoop().run()


if __name__ == "__main__":
    main()
