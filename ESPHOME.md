# ESPHome Integration Guide

This guide documents how to integrate ESPHome devices with `dbus-mqtt-switch`.
All examples below are minimal and functional — display code, diagnostics and WiFi config are omitted.

---

## Critical rules (read before anything else)

These are non-obvious behaviours discovered through testing:

1. **RGB values are pure color, NOT brightness-scaled.**
   `get_red()`, `get_green()`, `get_blue()` in ESPHome return the pure hue (0.0–1.0), independent of brightness.
   Brightness must be published as a separate `"dimming"` field. Venus OS handles them independently.

2. **CCT color temperature is in Kelvin, not mireds.**
   Venus OS GUI sends and expects color temperature in **Kelvin** (e.g. 2700 K, 6500 K).
   ESPHome's `cwww` platform works internally in **mireds** (153–500).
   You **must** convert: `mireds = 1000000 / kelvin` and `kelvin = 1000000 / mireds`.

3. **Use `default_transition_length: 0s` on all lights.**
   Transitions create a stream of intermediate states that fight with Venus OS GUI updates, causing feedback loops and flickering.

4. **Publish state on three events:** `on_state`, `on_connect`, and a periodic keepalive interval.
   Without the keepalive, the driver marks the device as disconnected after `timeout` seconds.

5. **For virtual lights (no hardware), use template outputs.**
   ESPHome's `light` components require outputs. Use `platform: template` with an empty `write_action` for purely virtual lights.

---

## MQTT payload reference

| Type | Payload |
|------|---------|
| 1 — Toggle | `{"state": 1}` |
| 2 — Dimmable | `{"state": 1, "dimming": 75}` |
| 11 — RGB | `{"state": 1, "red": 255, "green": 128, "blue": 0, "dimming": 75}` |
| 12 — CCT | `{"state": 1, "colortemp": 2700, "dimming": 75}` |
| 13 — RGBW | `{"state": 1, "red": 255, "green": 128, "blue": 0, "white": 50, "dimming": 75}` |

- `state`: `1` = on, `0` = off
- `dimming`: brightness 0–100 %
- `red`, `green`, `blue`: pure color 0–255 (not brightness-scaled)
- `white`: white channel 0–100 %
- `colortemp`: color temperature in **Kelvin**

The same format is used in both directions:
- Device → Cerbo (state topic)
- Cerbo → device (command topic, default: `<topic>/set`)

---

## Type 1 — Toggle (template switch)

A virtual on/off switch with no physical hardware output.

```yaml
globals:
  - id: light1_state
    type: bool
    restore_value: true
    initial_value: 'false'

switch:
  - platform: template
    name: "Switch 1"
    id: light1_switch
    lambda: 'return id(light1_state);'
    turn_on_action:
      - globals.set: { id: light1_state, value: 'true' }
      - if:
          condition: { mqtt.connected: }
          then:
            - mqtt.publish:
                topic: "home/switch/light1"
                payload: '{"state":1}'
    turn_off_action:
      - globals.set: { id: light1_state, value: 'false' }
      - if:
          condition: { mqtt.connected: }
          then:
            - mqtt.publish:
                topic: "home/switch/light1"
                payload: '{"state":0}'

mqtt:
  broker: 192.168.1.x
  on_connect:
    then:
      - mqtt.publish:
          topic: "home/switch/light1"
          payload: !lambda |-
            char buf[24];
            snprintf(buf, sizeof(buf), "{\"state\":%d}", id(light1_state) ? 1 : 0);
            return std::string(buf);
  on_message:
    - topic: "home/switch/light1/set"
      then:
        - lambda: |-
            json::parse_json(x, [&](JsonObject root) -> bool {
              if (!root.containsKey("state")) return false;
              id(light1_state) = root["state"].as<int>() != 0;
              return true;
            });
        - if:
            condition: { mqtt.connected: }
            then:
              - mqtt.publish:
                  topic: "home/switch/light1"
                  payload: !lambda |-
                    char buf[24];
                    snprintf(buf, sizeof(buf), "{\"state\":%d}", id(light1_state) ? 1 : 0);
                    return std::string(buf);

interval:
  - interval: 30s
    then:
      - if:
          condition: { mqtt.connected: }
          then:
            - mqtt.publish:
                topic: "home/switch/light1"
                payload: !lambda |-
                  char buf[24];
                  snprintf(buf, sizeof(buf), "{\"state\":%d}", id(light1_state) ? 1 : 0);
                  return std::string(buf);
```

---

## Type 2 — Dimmable (monochromatic PWM light)

```yaml
output:
  - platform: ledc
    pin: GPIO22
    id: pwm_output
    frequency: 1000Hz

light:
  - platform: monochromatic
    name: "Light 2"
    id: light2
    output: pwm_output
    default_transition_length: 0s
    on_state:
      then:
        - if:
            condition: { mqtt.connected: }
            then:
              - mqtt.publish:
                  topic: "home/switch/light2"
                  payload: !lambda |-
                    bool on  = id(light2).current_values.is_on();
                    float dim = id(light2).current_values.get_brightness() * 100.0f;
                    char buf[48];
                    snprintf(buf, sizeof(buf), "{\"state\":%d,\"dimming\":%.0f}", on ? 1 : 0, dim);
                    return std::string(buf);

mqtt:
  broker: 192.168.1.x
  on_connect:
    then:
      - mqtt.publish:
          topic: "home/switch/light2"
          payload: !lambda |-
            bool on  = id(light2).current_values.is_on();
            float dim = id(light2).current_values.get_brightness() * 100.0f;
            char buf[48];
            snprintf(buf, sizeof(buf), "{\"state\":%d,\"dimming\":%.0f}", on ? 1 : 0, dim);
            return std::string(buf);
  on_message:
    - topic: "home/switch/light2/set"
      then:
        - lambda: |-
            json::parse_json(x, [&](JsonObject root) -> bool {
              if (!root.containsKey("state")) return false;
              bool on = root["state"].as<int>() != 0;
              if (!on) {
                id(light2).turn_off().perform();
              } else {
                float dim = root.containsKey("dimming")
                  ? root["dimming"].as<float>() / 100.0f : 1.0f;
                auto call = id(light2).turn_on();
                call.set_brightness(std::max(0.0f, std::min(1.0f, dim)));
                call.perform();
              }
              return true;
            });

interval:
  - interval: 30s
    then:
      - if:
          condition: { mqtt.connected: }
          then:
            - mqtt.publish:
                topic: "home/switch/light2"
                payload: !lambda |-
                  bool on  = id(light2).current_values.is_on();
                  float dim = id(light2).current_values.get_brightness() * 100.0f;
                  char buf[48];
                  snprintf(buf, sizeof(buf), "{\"state\":%d,\"dimming\":%.0f}", on ? 1 : 0, dim);
                  return std::string(buf);
```

---

## Type 11 — RGB (WS2812 / addressable LED)

> **Important:** `get_red()`, `get_green()`, `get_blue()` return the pure hue (0.0–1.0).
> `get_brightness()` is separate. Always publish both.

```yaml
light:
  - platform: esp32_rmt_led_strip
    name: "Light 3 RGB"
    id: light3
    pin: GPIO8
    num_leds: 1
    rgb_order: RGB
    chipset: ws2812
    default_transition_length: 0s
    on_state:
      then:
        - if:
            condition: { mqtt.connected: }
            then:
              - mqtt.publish:
                  topic: "home/switch/light3"
                  payload: !lambda |-
                    bool on  = id(light3).current_values.is_on();
                    float r  = id(light3).current_values.get_red()   * 255.0f;
                    float g  = id(light3).current_values.get_green() * 255.0f;
                    float b  = id(light3).current_values.get_blue()  * 255.0f;
                    float dim = id(light3).current_values.get_brightness() * 100.0f;
                    char buf[96];
                    snprintf(buf, sizeof(buf),
                      "{\"state\":%d,\"red\":%.0f,\"green\":%.0f,\"blue\":%.0f,\"dimming\":%.0f}",
                      on ? 1 : 0, r, g, b, dim);
                    return std::string(buf);

mqtt:
  broker: 192.168.1.x
  on_connect:
    then:
      - mqtt.publish:
          topic: "home/switch/light3"
          payload: !lambda |-
            bool on  = id(light3).current_values.is_on();
            float r  = id(light3).current_values.get_red()   * 255.0f;
            float g  = id(light3).current_values.get_green() * 255.0f;
            float b  = id(light3).current_values.get_blue()  * 255.0f;
            float dim = id(light3).current_values.get_brightness() * 100.0f;
            char buf[96];
            snprintf(buf, sizeof(buf),
              "{\"state\":%d,\"red\":%.0f,\"green\":%.0f,\"blue\":%.0f,\"dimming\":%.0f}",
              on ? 1 : 0, r, g, b, dim);
            return std::string(buf);
  on_message:
    - topic: "home/switch/light3/set"
      then:
        - lambda: |-
            json::parse_json(x, [&](JsonObject root) -> bool {
              if (!root.containsKey("state")) return false;
              bool on = root["state"].as<int>() != 0;
              if (!on) {
                id(light3).turn_off().perform();
              } else {
                float r   = root.containsKey("red")     ? root["red"].as<float>()   / 255.0f : 1.0f;
                float g   = root.containsKey("green")   ? root["green"].as<float>() / 255.0f : 1.0f;
                float b   = root.containsKey("blue")    ? root["blue"].as<float>()  / 255.0f : 1.0f;
                float dim = root.containsKey("dimming") ? root["dimming"].as<float>() / 100.0f : 1.0f;
                auto call = id(light3).turn_on();
                call.set_rgb(r, g, b);
                call.set_brightness(std::max(0.0f, std::min(1.0f, dim)));
                call.perform();
              }
              return true;
            });

interval:
  - interval: 30s
    then:
      - if:
          condition: { mqtt.connected: }
          then:
            - mqtt.publish:
                topic: "home/switch/light3"
                payload: !lambda |-
                  bool on  = id(light3).current_values.is_on();
                  float r  = id(light3).current_values.get_red()   * 255.0f;
                  float g  = id(light3).current_values.get_green() * 255.0f;
                  float b  = id(light3).current_values.get_blue()  * 255.0f;
                  float dim = id(light3).current_values.get_brightness() * 100.0f;
                  char buf[96];
                  snprintf(buf, sizeof(buf),
                    "{\"state\":%d,\"red\":%.0f,\"green\":%.0f,\"blue\":%.0f,\"dimming\":%.0f}",
                    on ? 1 : 0, r, g, b, dim);
                  return std::string(buf);
```

---

## Type 12 — CCT (cold/warm white)

> **Important:** Venus OS sends/expects color temperature in **Kelvin** (2700–6500 K).
> ESPHome's `cwww` platform uses **mireds** internally.
> You must convert in both directions:
> - Receiving from Venus OS: `mireds = 1000000 / kelvin`
> - Publishing to Venus OS: `kelvin = 1000000 / mireds`

```yaml
output:
  # Use real hardware outputs or template outputs for virtual testing
  - platform: template
    id: light4_cold_out
    type: float
    write_action: { lambda: return; }   # replace with real hardware output
  - platform: template
    id: light4_warm_out
    type: float
    write_action: { lambda: return; }   # replace with real hardware output

light:
  - platform: cwww
    name: "Light 4 CCT"
    id: light4
    cold_white: light4_cold_out
    warm_white: light4_warm_out
    cold_white_color_temperature: 153 mireds   # 6500 K
    warm_white_color_temperature: 500 mireds   # 2000 K
    default_transition_length: 0s
    on_state:
      then:
        - if:
            condition: { mqtt.connected: }
            then:
              - mqtt.publish:
                  topic: "home/switch/light4"
                  payload: !lambda |-
                    bool on = id(light4).current_values.is_on();
                    float ct_mireds = id(light4).current_values.get_color_temperature();
                    // Convert mireds → Kelvin for Venus OS
                    float ct_kelvin = (ct_mireds > 0.0f) ? (1000000.0f / ct_mireds) : 2700.0f;
                    float dim = id(light4).current_values.get_brightness() * 100.0f;
                    char buf[80];
                    snprintf(buf, sizeof(buf),
                      "{\"state\":%d,\"colortemp\":%.0f,\"dimming\":%.0f}",
                      on ? 1 : 0, ct_kelvin, dim);
                    return std::string(buf);

mqtt:
  broker: 192.168.1.x
  on_connect:
    then:
      - mqtt.publish:
          topic: "home/switch/light4"
          payload: !lambda |-
            bool on = id(light4).current_values.is_on();
            float ct_mireds = id(light4).current_values.get_color_temperature();
            float ct_kelvin = (ct_mireds > 0.0f) ? (1000000.0f / ct_mireds) : 2700.0f;
            float dim = id(light4).current_values.get_brightness() * 100.0f;
            char buf[80];
            snprintf(buf, sizeof(buf),
              "{\"state\":%d,\"colortemp\":%.0f,\"dimming\":%.0f}",
              on ? 1 : 0, ct_kelvin, dim);
            return std::string(buf);
  on_message:
    - topic: "home/switch/light4/set"
      then:
        - lambda: |-
            json::parse_json(x, [&](JsonObject root) -> bool {
              if (!root.containsKey("state")) return false;
              bool on = root["state"].as<int>() != 0;
              if (!on) {
                id(light4).turn_off().perform();
              } else {
                auto call = id(light4).turn_on();
                if (root.containsKey("dimming"))
                  call.set_brightness(std::max(0.0f, std::min(1.0f,
                    root["dimming"].as<float>() / 100.0f)));
                if (root.containsKey("colortemp")) {
                  float kelvin = root["colortemp"].as<float>();
                  // Convert Kelvin → mireds for ESPHome cwww
                  if (kelvin > 0.0f)
                    call.set_color_temperature(1000000.0f / kelvin);
                }
                call.perform();
              }
              return true;
            });

interval:
  - interval: 30s
    then:
      - if:
          condition: { mqtt.connected: }
          then:
            - mqtt.publish:
                topic: "home/switch/light4"
                payload: !lambda |-
                  bool on = id(light4).current_values.is_on();
                  float ct_mireds = id(light4).current_values.get_color_temperature();
                  float ct_kelvin = (ct_mireds > 0.0f) ? (1000000.0f / ct_mireds) : 2700.0f;
                  float dim = id(light4).current_values.get_brightness() * 100.0f;
                  char buf[80];
                  snprintf(buf, sizeof(buf),
                    "{\"state\":%d,\"colortemp\":%.0f,\"dimming\":%.0f}",
                    on ? 1 : 0, ct_kelvin, dim);
                  return std::string(buf);
```

---

## Type 13 — RGBW

> `get_white()` returns 0.0–1.0. Multiply by 100 for the `white` field (0–100 %).

```yaml
output:
  - platform: template
    id: light5_r_out
    type: float
    write_action: { lambda: return; }   # replace with real hardware output
  - platform: template
    id: light5_g_out
    type: float
    write_action: { lambda: return; }
  - platform: template
    id: light5_b_out
    type: float
    write_action: { lambda: return; }
  - platform: template
    id: light5_w_out
    type: float
    write_action: { lambda: return; }

light:
  - platform: rgbw
    name: "Light 5 RGBW"
    id: light5
    red:   light5_r_out
    green: light5_g_out
    blue:  light5_b_out
    white: light5_w_out
    default_transition_length: 0s
    on_state:
      then:
        - if:
            condition: { mqtt.connected: }
            then:
              - mqtt.publish:
                  topic: "home/switch/light5"
                  payload: !lambda |-
                    bool on  = id(light5).current_values.is_on();
                    float r  = id(light5).current_values.get_red()   * 255.0f;
                    float g  = id(light5).current_values.get_green() * 255.0f;
                    float b  = id(light5).current_values.get_blue()  * 255.0f;
                    float w  = id(light5).current_values.get_white() * 100.0f;
                    float dim = id(light5).current_values.get_brightness() * 100.0f;
                    char buf[128];
                    snprintf(buf, sizeof(buf),
                      "{\"state\":%d,\"red\":%.0f,\"green\":%.0f,\"blue\":%.0f,\"white\":%.0f,\"dimming\":%.0f}",
                      on ? 1 : 0, r, g, b, w, dim);
                    return std::string(buf);

mqtt:
  broker: 192.168.1.x
  on_connect:
    then:
      - mqtt.publish:
          topic: "home/switch/light5"
          payload: !lambda |-
            bool on  = id(light5).current_values.is_on();
            float r  = id(light5).current_values.get_red()   * 255.0f;
            float g  = id(light5).current_values.get_green() * 255.0f;
            float b  = id(light5).current_values.get_blue()  * 255.0f;
            float w  = id(light5).current_values.get_white() * 100.0f;
            float dim = id(light5).current_values.get_brightness() * 100.0f;
            char buf[128];
            snprintf(buf, sizeof(buf),
              "{\"state\":%d,\"red\":%.0f,\"green\":%.0f,\"blue\":%.0f,\"white\":%.0f,\"dimming\":%.0f}",
              on ? 1 : 0, r, g, b, w, dim);
            return std::string(buf);
  on_message:
    - topic: "home/switch/light5/set"
      then:
        - lambda: |-
            json::parse_json(x, [&](JsonObject root) -> bool {
              if (!root.containsKey("state")) return false;
              bool on = root["state"].as<int>() != 0;
              if (!on) {
                id(light5).turn_off().perform();
              } else {
                auto call = id(light5).turn_on();
                if (root.containsKey("red") && root.containsKey("green") && root.containsKey("blue"))
                  call.set_rgb(root["red"].as<float>()   / 255.0f,
                               root["green"].as<float>() / 255.0f,
                               root["blue"].as<float>()  / 255.0f);
                if (root.containsKey("white"))
                  call.set_white(std::max(0.0f, std::min(1.0f,
                    root["white"].as<float>() / 100.0f)));
                if (root.containsKey("dimming"))
                  call.set_brightness(std::max(0.0f, std::min(1.0f,
                    root["dimming"].as<float>() / 100.0f)));
                call.perform();
              }
              return true;
            });

interval:
  - interval: 30s
    then:
      - if:
          condition: { mqtt.connected: }
          then:
            - mqtt.publish:
                topic: "home/switch/light5"
                payload: !lambda |-
                  bool on  = id(light5).current_values.is_on();
                  float r  = id(light5).current_values.get_red()   * 255.0f;
                  float g  = id(light5).current_values.get_green() * 255.0f;
                  float b  = id(light5).current_values.get_blue()  * 255.0f;
                  float w  = id(light5).current_values.get_white() * 100.0f;
                  float dim = id(light5).current_values.get_brightness() * 100.0f;
                  char buf[128];
                  snprintf(buf, sizeof(buf),
                    "{\"state\":%d,\"red\":%.0f,\"green\":%.0f,\"blue\":%.0f,\"white\":%.0f,\"dimming\":%.0f}",
                    on ? 1 : 0, r, g, b, w, dim);
                  return std::string(buf);
```

---

## MQTT broker

The Cerbo GX runs a local MQTT broker (`flashmq`) on `127.0.0.1:1883`.
Point your ESPHome device to the **Cerbo's IP address** on port `1883` — no authentication required on the local network by default.

```yaml
mqtt:
  broker: 192.168.1.x   # Cerbo GX IP
  port: 1883
  client_id: my_esphome_device
  discovery: false
  keepalive: 120s        # increase if on a slow network to avoid PING_RESP disconnects
```

Setting `keepalive: 120s` prevents disconnections on mobile/VPN networks where PING responses can be slow.
