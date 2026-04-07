#!/usr/bin/env python3
"""
dbus-mqtt-switch  —  Venus OS driver v0.6.13
============================================
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

VERSION = "0.6.13"

# Type labels — left side of "TypeLabel: CustomName" in the device row
TYPE_LABELS = {
    1:  "Toggle",
    2:  "Dimmable",
    11: "RGB",
    12: "CCT",
    13: "RGBW",
}

# Status bitmask values for /SwitchableOutput/output_1/Status
# Bit 0 = Powered (output on). 0x00 = Off, 0x01 = On.
STATUS_OFF = 0x00
STATUS_ON  = 0x01

# ShowUIControl bitmask: 1=All UIs (local+VRM), 2=Local only, 4=Remote only
SHOW_UI_LABELS = {1: "All UIs", 2: "Local only", 4: "Remote only"}

# Type custom names — default CustomName / output label per type
TYPE_CUSTOM_NAMES = {
    1:  "Virtual Toggle",
    2:  "Virtual Dimmer",
    11: "Virtual RGB",
    12: "Virtual CCT",
    13: "Virtual RGBW",
}


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
# CustomName = output label shown inside the switch card, defaults to type-based name
# e.g. type 1 → "Virtual Toggle", type 2 → "Virtual Dimmer", etc.
custom_name  = config.get("DEFAULT", "custom_name",
                           fallback=TYPE_CUSTOM_NAMES.get(switch_type, "Virtual Toggle"))

try:
    topic_state = config.get("MQTT", "topic")
except Exception:
    print('ERROR: "topic" is missing from [MQTT] section in config.ini. Restarting in 60s.')
    sleep(60)
    sys.exit()
topic_command = config.get("MQTT", "topic_command", fallback=topic_state + "/set")

# Optional availability (LWT) topic — broker publishes this when device connects/disconnects.
# Enables instant offline detection without relying solely on the application-level timeout.
# Standard payloads follow the ESPHome / Home Assistant MQTT convention.
# Examples:
#   ESPHome:  availability_topic = c6venustest/status  (payload_available=online, default)
#   Shelly:   availability_topic = shellies/shelly1-ABC/online  (payload_available=true)
#   Tasmota:  availability_topic = tele/tasmota_ABC/LWT  (payload_available=Online)
availability_topic   = config.get("MQTT", "availability_topic",   fallback="")
payload_available    = config.get("MQTT", "payload_available",    fallback="online")
payload_unavailable  = config.get("MQTT", "payload_unavailable",  fallback="offline")

logging.info(f"dbus-mqtt-switch v{VERSION} — config: {config_file}")
logging.info(f"  device='{device_name}' instance={device_instance} type={switch_type}")
logging.info(f"  topics: state='{topic_state}'  command='{topic_command}'")


# ── Advanced tuning (optional config keys in [DEFAULT]) ──────────────────────
# All values read from the config with sensible defaults matching previous
# behaviour. Most users never need to touch these; documented in the README
# under "Advanced tuning" for users with unusual MQTT brokers, slow devices,
# or very long ESPHome transition animations.
CMD_COOLDOWN     = int(config.get("DEFAULT", "cmd_cooldown",     fallback="3"))
# seconds to ignore MQTT feedback after a GUI command. Matches ESPHome
# transition_length (1 s default) + margin. Prevents slider ping-pong during
# device transition animations. Raise if you use longer transition_length.
RECONNECT_BYPASS = int(config.get("DEFAULT", "reconnect_bypass", fallback="5"))
# seconds after a reconnect event (LWT online or /Connected 0→1) during which
# the command cooldown is bypassed. Defends against the fast-cycle race where
# user clicks slider → cooldown starts → device unplugged → device reconnects
# fast (< CMD_COOLDOWN) → first retained state message would otherwise be
# silently swallowed. Raise if your device takes longer to publish retained
# state after reboot.
_poll_raw = config.get("DEFAULT", "resubscribe_poll", fallback="2,5,10")
try:
    RESUBSCRIBE_POLL = [int(s.strip()) for s in _poll_raw.split(",") if s.strip()]
except ValueError:
    RESUBSCRIBE_POLL = [2, 5, 10]
# comma-separated list of seconds at which to re-subscribe to topic_state
# after a reconnect event. Each re-subscribe forces the broker to redeliver
# the retained state, catching any post-reconnect publish that happened after
# our initial subscribe. Default "2,5,10". Set to "" to disable polling.
LWT_WAIT_TIMEOUT = int(config.get("DEFAULT", "lwt_wait_timeout", fallback="15"))
# seconds to wait at startup for an LWT message on the availability topic
# before assuming the device is online and proceeding with registration.
# Only used when availability_topic is configured. Raise on unusual brokers
# that take longer than 15 s to deliver retained messages.
EXIT_SLEEP       = int(config.get("DEFAULT", "exit_sleep",       fallback="1"))
# seconds to sleep before sys.exit(0) after LWT offline / timeout. Kept short
# so daemontools restarts the driver quickly — the new process blocks in the
# LWT wait loop until the device is back, so this is NOT a daemontools spin.


# ── State variables ────────────────────────────────────────────────────────────

connected        = 0
last_changed     = 0
last_updated     = 0
last_cmd_time    = 0       # timestamp of last GUI→MQTT command
lwt_offline      = False   # True when LWT "unavailable" payload received; cleared on "available"
lwt_known        = False   # True once any LWT message has been received
fresh_state      = False   # True when a state message has arrived AFTER the last LWT "online".
                           # Used by main() to avoid registering on dbus with a stale retained
                           # state from BEFORE the device went offline (prevents the "ghost
                           # dimmer value" bug when reconnecting after a user touched the slider
                           # during the dead window).
last_reconnect_event = 0   # timestamp of the last reconnect event (LWT online or
                           # /Connected 0 → 1 transition). Used to schedule re-subscribes
                           # to topic_state at 2 s / 5 s / 10 s after the event — each
                           # re-subscribe forces the broker to redeliver the retained
                           # state, catching any post-reconnect publish that may have
                           # happened after our initial subscribe. Also used by the
                           # cooldown-bypass rule (see RECONNECT_BYPASS).
mainloop         = None    # GLib.MainLoop reference — set in main(), used to exit on LWT offline
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
    global connected, last_cmd_time
    if reason_code == 0:
        logging.info("MQTT: connected to broker")
        connected = 1
        # Clear any stale cooldown left over from before the broker (re)connect.
        # Safety net: ensures the very first state message after reconnect is
        # never suppressed by a cooldown that was started before we lost the
        # broker (e.g. ghost commands sent during the dead window).
        last_cmd_time = 0
        client.subscribe(topic_state)
        logging.info(f"MQTT: subscribed to '{topic_state}'")
        if availability_topic:
            client.subscribe(availability_topic)
            logging.info(f"MQTT: subscribed to availability topic '{availability_topic}'")
    else:
        logging.error(f"MQTT: connection failed, rc={reason_code}")


def _resubscribe_state():
    """
    Force the broker to redeliver the retained message on topic_state by
    re-subscribing. Safe to call from any thread (paho's subscribe is
    thread-safe). Used by the reconnect polling mechanism to catch any
    post-reconnect state publish that may have happened after our initial
    subscribe.

    Returns False so GLib.timeout_add does not repeat the call.
    """
    global mqtt_client
    try:
        if mqtt_client is not None and connected == 1:
            mqtt_client.subscribe(topic_state)
            logging.debug(f"MQTT: re-subscribed to '{topic_state}' (poll for fresh retained state)")
    except Exception as e:
        logging.warning(f"MQTT: re-subscribe failed: {e}")
    return False  # single-shot


def _schedule_resubscribe_polling():
    """
    Schedule re-subscribes to topic_state at each interval in RESUBSCRIBE_POLL
    (default [2, 5, 10] seconds) after a reconnect event (LWT online or
    /Connected 0 → 1). Each re-subscribe forces the broker to redeliver the
    retained state, so if the device finished booting / publishing AFTER our
    initial subscribe, we still catch the fresh retained state without
    waiting for the next state-change event.

    The list is configurable via the `resubscribe_poll` key in [DEFAULT];
    set to empty to disable polling.
    """
    if not RESUBSCRIBE_POLL:
        return
    for secs in RESUBSCRIBE_POLL:
        GLib.timeout_add_seconds(secs, _resubscribe_state)
    logging.debug(
        f"MQTT: scheduled re-subscribe polling at "
        f"{'/'.join(f'+{s}s' for s in RESUBSCRIBE_POLL)}")


def on_message(client, userdata, msg):
    global last_changed, last_cmd_time, lwt_offline, fresh_state, last_reconnect_event
    global state, dimming, red, green, blue, colortemp, white
    try:
        if not msg.payload:
            return

        # ── Availability / LWT topic ───────────────────────────────────────────
        if availability_topic and msg.topic == availability_topic:
            global lwt_known
            avail_payload = msg.payload.decode("utf-8", errors="ignore").strip()
            lwt_known = True
            if avail_payload == payload_unavailable:
                lwt_offline = True
                fresh_state = False   # any state we might hold is now stale
                # Wipe all cached value globals so a subsequent partial state
                # message cannot silently re-use "ghost" values from before
                # the device went offline.
                state = None
                dimming = None
                red = green = blue = None
                colortemp = None
                white = None
                logging.warning(f"LWT: device offline ('{avail_payload}') — caches cleared")
            elif avail_payload == payload_available:
                lwt_offline = False
                last_cmd_time = 0     # clear any stale cooldown from ghost commands
                last_reconnect_event = time()  # mark for cooldown bypass + polling
                # Note: we deliberately do NOT reset fresh_state here.
                # If the retained state message happens to be processed before
                # this "online" message (ordering depends on the broker), the
                # state is actually fresh (device is currently online) and we
                # should keep it. If the retained state arrives AFTER this
                # message, the state-handling branch below will set fresh_state
                # anyway. If a stale "offline" → "online" transition happens,
                # globals were already wiped by the "offline" branch above,
                # so there is nothing stale to keep.
                logging.info(f"LWT: device online ('{avail_payload}') — ready to sync")
                # Schedule defensive re-subscribes so that if the device's
                # post-reconnect state publish arrives AFTER our current
                # subscribe, we force the broker to redeliver it.
                try:
                    _schedule_resubscribe_polling()
                except Exception as e:
                    logging.warning(f"re-subscribe schedule failed: {e}")
            else:
                logging.debug(f"LWT: unknown payload '{avail_payload}' on {msg.topic}")
            return

        if msg.topic != topic_state:
            return

        # Device is currently offline — any state we receive right now is a
        # stale retained message from before the disconnect. Ignore entirely
        # so nothing leaks into the globals or into fresh_state.
        if lwt_offline:
            logging.debug("State msg ignored: lwt_offline is true (stale retained)")
            return

        payload = json.loads(msg.payload)
        now = time()
        # Any state message received while the device is online reflects the
        # real current device state and is considered "fresh".
        fresh_state = True

        # Command cooldown: after a GUI command is sent, ignore ALL MQTT feedback
        # (state + dimming + color) for CMD_COOLDOWN seconds.
        # Prevents ping-pong: ESPHome reports state=1 while transitioning to OFF,
        # which would overwrite the 0 already written to dbus by _handlechangedvalue.
        # _handlechangedvalue is authoritative during cooldown; MQTT syncs after.
        in_cooldown = (now - last_cmd_time) < CMD_COOLDOWN

        # Reconnect bypass: if we just saw a reconnect event (LWT online or
        # /Connected 0 → 1 transition) within the last RECONNECT_BYPASS seconds,
        # the cooldown no longer reflects reality — the device just restarted
        # with its own boot defaults (RESTORE_DEFAULT_ON, etc.) and whatever
        # the user touched during the dead window is gone. The ESP's first
        # post-reconnect state publish IS the truth and must not be swallowed.
        if in_cooldown and last_reconnect_event > 0 \
                and (now - last_reconnect_event) < RECONNECT_BYPASS:
            logging.info(
                f"MQTT rx: cooldown bypassed — reconnect "
                f"{now - last_reconnect_event:.1f}s ago < {RECONNECT_BYPASS}s")
            in_cooldown = False
            last_cmd_time = 0  # fully clear so subsequent msgs also pass

        if not in_cooldown:
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
        else:
            logging.debug(f"MQTT rx: cooldown ({now - last_cmd_time:.1f}s < {CMD_COOLDOWN}s) — all updates suppressed")

        last_changed = int(now)
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
        # /CustomName = device identifier shown in breadcrumb (e.g. "Switch 2")
        # kept separate from output_1/Settings/CustomName (type label, e.g. "Virtual Dimmer")
        self._dbusservice.add_path("/CustomName",          productname)
        self._dbusservice.add_path("/Serial",              f"mqtt_{deviceinstance}")
        self._dbusservice.add_path("/Connected",           1)

        # NOTE: top-level /State is intentionally NOT added — Node-RED
        # dbus-victron-virtual does not expose it for switch devices.

        # SwitchableOutput channel — path naming matches Node-RED virtual switch (output_1)
        # Name = type label (left side of "Dimmable: Virtual Switch 2" in the device row)
        type_label = TYPE_LABELS.get(switch_type, "Toggle")
        self._dbusservice.add_path("/SwitchableOutput/output_1/Name", type_label)
        # Status = read-only hardware output state. Bit 0 = Powered.
        # 0x00 = Off, 0x01 = On (Powered).  Text formatter shows "Off"/"On".
        self._dbusservice.add_path(
            "/SwitchableOutput/output_1/Status", STATUS_OFF,
            gettextcallback=lambda p, v: "On" if v else "Off")

        # Settings — GUI v2 + VRM switch pane requires these to show the device
        # ShowUIControl bitmask: 1=All UIs (local+VRM), 2=Local only, 4=Remote only
        self._dbusservice.add_path(
            "/SwitchableOutput/output_1/Settings/ShowUIControl", 1,
            writeable=True,
            gettextcallback=lambda p, v: SHOW_UI_LABELS.get(int(v), str(v)),
            onchangecallback=self._handlechangedvalue)
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
            gettextcallback=lambda p, v: TYPE_LABELS.get(int(v), str(v)),
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
        global last_changed, last_updated, last_cmd_time, last_reconnect_event
        now = int(time())

        if last_changed != last_updated:
            if state is not None:
                self._dbusservice["/SwitchableOutput/output_1/State"] = state
                self._dbusservice["/SwitchableOutput/output_1/Status"] = STATUS_ON if state else STATUS_OFF
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

            # Mark as connected when receiving MQTT data.
            # When transitioning from disconnected → connected, also clear any
            # stale cooldown so the very first incoming state message is never
            # suppressed (defends against ghost commands left over from the
            # 30 s window before the device disappeared).
            if self._dbusservice["/Connected"] != 1:
                self._dbusservice["/Connected"] = 1
                last_cmd_time = 0
                last_reconnect_event = time()
                logging.info("Device connected (MQTT data received) — cooldown cleared")
                # Defensive polling: re-subscribe a few times over the next 10 s
                # so any post-reconnect retained state that the ESP publishes
                # AFTER we already subscribed is delivered to us, not missed.
                try:
                    _schedule_resubscribe_polling()
                except Exception as e:
                    logging.warning(f"re-subscribe schedule failed: {e}")

            index = (self._dbusservice["/UpdateIndex"] + 1) % 256
            self._dbusservice["/UpdateIndex"] = index

            logging.debug(f"dbus updated: state={state} dimming={dimming} rgb=({red},{green},{blue}) ct={colortemp} w={white}")
            last_updated = last_changed

        # LWT: device went offline — mark disconnected immediately (blocks ghost commands),
        # then quit the GLib main loop so the process exits and the device disappears from GUI.
        # Daemontools restarts the process; on restart it waits for LWT "online" before
        # registering with dbus, so the device stays gone until the device is reachable again.
        if lwt_offline:
            if self._dbusservice["/Connected"] != 0:
                self._dbusservice["/Connected"] = 0
                self._dbusservice["/SwitchableOutput/output_1/State"]  = 0
                self._dbusservice["/SwitchableOutput/output_1/Status"] = STATUS_OFF
                # Also zero all value paths so the outgoing dbus service snapshot
                # never carries user-touched "ghost" values into the GUI cache.
                self._zero_value_paths()
            logging.warning("LWT: device offline — stopping event loop (device will disappear from GUI)")
            if mainloop and mainloop.is_running():
                mainloop.quit()
            return True

        # Timeout: no MQTT received within timeout seconds → device is offline.
        # Works independently of LWT — based purely on MQTT silence.
        # With availability_topic: acts as safety net (LWT is faster).
        # Without availability_topic: primary offline detection mechanism.
        # In both cases: exits the process so the device disappears from GUI.
        # (set timeout=0 in config.ini to disable)
        if timeout != 0 and last_changed != 0 and (now - last_changed) > timeout:
            if self._dbusservice["/Connected"] != 0:
                self._dbusservice["/Connected"] = 0
                self._dbusservice["/SwitchableOutput/output_1/State"]  = 0
                self._dbusservice["/SwitchableOutput/output_1/Status"] = STATUS_OFF
                self._zero_value_paths()
            logging.warning(
                f"Timeout of {timeout}s exceeded — stopping event loop (device will disappear from GUI)")
            if mainloop and mainloop.is_running():
                mainloop.quit()
            return True

        return True

    def _zero_value_paths(self):
        """
        Zero the Dimming / LightControls brightness component on the running
        dbus service, without touching State or Status (those are handled by
        the caller).  Used both by _snap_to_offline (ghost-command revert) and
        by the LWT/timeout branches of _update (so the final snapshot the GUI
        sees before the device disappears never carries stale user-touched
        values into its cache).
        """
        if switch_type in (2, 11, 12, 13):
            try:
                self._dbusservice["/SwitchableOutput/output_1/Dimming"] = 0.0
            except Exception:
                pass
        if switch_type in (11, 12, 13):
            try:
                lc = self._dbusservice["/SwitchableOutput/output_1/LightControls"]
                # Force brightness component (index 2) to 0 — keep hue/sat/ct/white
                self._dbusservice["/SwitchableOutput/output_1/LightControls"] = [
                    lc[0], lc[1], 0.0, lc[3], lc[4]]
            except Exception:
                pass

    def _snap_to_offline(self):
        """
        Revert all output paths to OFF after a rejected ghost command.
        Scheduled via GLib.idle_add so it runs after the callback returns,
        overwriting whatever value Venus OS just wrote to dbus.
        Returns False so GLib does not repeat the call.
        """
        self._dbusservice["/SwitchableOutput/output_1/State"]  = 0
        self._dbusservice["/SwitchableOutput/output_1/Status"] = STATUS_OFF
        # Also snap any value path the user may have touched, so the GUI
        # slider/colour wheel reverts at the same time as the on/off square.
        self._zero_value_paths()
        return False

    def _handlechangedvalue(self, path, value):
        """
        Called when Venus OS GUI writes a value (user toggles switch, moves slider,
        or changes RGB).  Publishes the command to the device via MQTT.
        """
        global mqtt_client, state, dimming, red, green, blue, colortemp, white, last_cmd_time
        try:
            # Block commands when the device is offline.
            # Venus OS has already written the new value to dbus before this callback fires,
            # so we schedule an immediate revert (_snap_to_offline) via GLib.idle_add.
            # This causes the GUI to snap back to OFF on the next event-loop tick,
            # making it clear to the user that no command was sent.
            if self._dbusservice["/Connected"] == 0:
                logging.warning(f"Device disconnected — GUI command rejected, reverting to OFF (path={path})")
                GLib.idle_add(self._snap_to_offline)
                return True

            if path == "/SwitchableOutput/output_1/State":
                state = int(value)
                self._dbusservice["/SwitchableOutput/output_1/Status"] = STATUS_ON if state else STATUS_OFF
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
                last_cmd_time = time()
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
                last_cmd_time = time()
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
                last_cmd_time = time()
                mqtt_client.publish(topic_command, json.dumps(payload))
                logging.info(f"GUI→MQTT [{topic_command}]: {payload}")

            elif path == "/SwitchableOutput/output_1/Settings/Type":
                logging.info(f"Type changed to {value} via GUI (requires driver restart)")

        except Exception as e:
            logging.error(f"_handlechangedvalue error: {e}")
        return True  # accept the change


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    global mqtt_client, mainloop
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

    # ── LWT availability check ─────────────────────────────────────────────────
    # If availability_topic is configured, wait until the broker confirms the device
    # is online before registering with dbus.  This means the device only appears in
    # the Venus OS GUI when it is actually reachable — it disappears when offline.
    #
    # Behaviour:
    #   - On first start / restart: wait up to 15 s for any LWT message.
    #     If "offline" → stay in wait loop until "online" arrives.
    #     If no LWT received in 15 s → assume online and proceed (safe fallback).
    #   - While waiting, no dbus service exists → device not visible in GUI.
    if availability_topic:
        logging.info(f"Waiting for device availability on '{availability_topic}'...")
        wait_start = time()
        while not lwt_known:
            if time() - wait_start > LWT_WAIT_TIMEOUT:
                logging.warning(
                    f"No LWT message received in {LWT_WAIT_TIMEOUT} s — "
                    f"assuming device is online, proceeding")
                break
            sleep(1)
        if lwt_known and lwt_offline:
            logging.info("Device is currently offline — waiting for it to come online (not registering with dbus yet)")
            while lwt_offline:
                sleep(2)
            logging.info("Device is now online — waiting for fresh state before registering with dbus")

    # Wait for a FRESH state message (one that arrived AFTER the last LWT "online")
    # before registering with dbus. Prevents registering with a stale retained value
    # from before the device went offline — which is what caused the "ghost dimmer
    # value" bug after reconnecting when the user had touched the slider during the
    # dead window. Fall back to "any state" if no availability_topic is configured.
    logging.info(f"Waiting for fresh MQTT state on '{topic_state}'...")
    i = 0
    if availability_topic:
        # Only accept a state message received AFTER the LWT "online" flipped fresh_state back to False
        while not fresh_state:
            if timeout != 0 and timeout <= (i * 2):
                logging.warning("No fresh state received — starting with last-known or default")
                break
            sleep(2)
            i += 1
    else:
        # No availability_topic — legacy behaviour: accept any state
        while state is None:
            if timeout != 0 and timeout <= (i * 2):
                logging.warning("No initial state received — starting with state=0")
                break
            sleep(2)
            i += 1

    # Text formatters for dbus paths — match Node-RED dbus-victron-virtual labels
    def _state_fmt(p, v): return "On" if v else "Off" if v is not None else "--"
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

    # After registration, arm a defensive re-subscribe polling round. This
    # catches the post-restart scenario where the driver subscribed and got
    # a (possibly stale) retained state, but the device then published a
    # fresher retained state a few seconds later (because the ESP was still
    # booting when we first subscribed). Without this, we would only see the
    # fresher state on the next natural state-change event.
    global last_reconnect_event
    last_reconnect_event = time()
    _schedule_resubscribe_polling()

    logging.info("Registered on dbus — running event loop")
    mainloop = GLib.MainLoop()
    mainloop.run()

    # ── Post-mainloop: device went offline (LWT) ───────────────────────────────
    # The event loop only exits when _update() calls mainloop.quit() after receiving
    # an LWT "offline" message.  We want the process to restart quickly so the
    # driver can latch onto the ESP's post-reconnect retained state as soon as
    # possible — this is critical for the fast-cycle case (user unplugs + plugs
    # back within ~10 s). The restart loop is NOT a daemontools spin because the
    # LWT wait-loop in main() will block the new process on the availability
    # topic until the device is back, so a short sleep is sufficient.
    logging.warning(
        f"Event loop stopped — device offline. "
        f"Exiting in {EXIT_SLEEP} s (daemontools will restart)...")
    sleep(EXIT_SLEEP)
    sys.exit(0)


if __name__ == "__main__":
    main()
