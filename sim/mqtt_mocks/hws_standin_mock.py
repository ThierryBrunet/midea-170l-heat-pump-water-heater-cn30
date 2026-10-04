"""MQTT Discovery stand-in for Chromagen HP170 until EW-11A + HACS are live."""

from __future__ import annotations

import json
import os
import sys
import time

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("Install paho-mqtt: pip install paho-mqtt", file=sys.stderr)
    raise

DISC = "homeassistant"
NODE = "chromagen_hp170"


def main() -> int:
    host = os.environ.get("MQTT_HOST")
    if not host:
        print("MQTT_HOST is not set", file=sys.stderr)
        return 2
    port = int(os.environ.get("MQTT_PORT", "1883"))
    user = os.environ.get("MQTT_USER")
    password = os.environ.get("MQTT_PASSWORD")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="hws-standin")
    if user:
        client.username_pw_set(user, password)
    client.will_set(f"{NODE}/lwt", "offline", retain=True)
    client.connect(host, port, 60)

    device = {
        "identifiers": [NODE],
        "name": "Chromagen HP170",
        "model": "RSJ-15/190RDN3-C",
        "manufacturer": "Midea/Chromagen",
    }
    avail = {"topic": f"{NODE}/lwt"}

    water = {
        "name": "Chromagen HP170",
        "unique_id": "chromagen_hp170",
        "object_id": "chromagen_hp170",
        "modes": ["off", "eco", "performance", "electric"],
        "mode_command_topic": f"{NODE}/mode/set",
        "mode_state_topic": f"{NODE}/mode",
        "temperature_command_topic": f"{NODE}/target/set",
        "temperature_state_topic": f"{NODE}/target",
        "current_temperature_topic": f"{NODE}/current",
        "min_temp": 60,
        "max_temp": 70,
        "temp_step": 1,
        "availability": [avail],
        "device": device,
    }
    client.publish(f"{DISC}/water_heater/{NODE}/config", json.dumps(water), retain=True)

    for key, name, unit, dc in (
        ("t5u", "Tank top", "°C", "temperature"),
        ("t5l", "Tank bottom", "°C", "temperature"),
        ("t3", "Condenser", "°C", "temperature"),
        ("t4", "Outdoor", "°C", "temperature"),
    ):
        cfg = {
            "name": name,
            "unique_id": f"{NODE}_{key}",
            "object_id": f"chromagen_hp170_{key}",
            "state_topic": f"{NODE}/{key}",
            "unit_of_measurement": unit,
            "device_class": dc,
            "state_class": "measurement",
            "availability": [avail],
            "device": device,
        }
        client.publish(f"{DISC}/sensor/{NODE}_{key}/config", json.dumps(cfg), retain=True)

    sw = {
        "name": "Sanitize mode",
        "unique_id": f"{NODE}_sanitize",
        "object_id": "chromagen_hp170_sanitize",
        "command_topic": f"{NODE}/sanitize/set",
        "state_topic": f"{NODE}/sanitize",
        "payload_on": "ON",
        "payload_off": "OFF",
        "availability": [avail],
        "device": device,
    }
    client.publish(f"{DISC}/switch/{NODE}_sanitize/config", json.dumps(sw), retain=True)

    client.publish(f"{NODE}/lwt", "online", retain=True)
    client.publish(f"{NODE}/mode", "eco", retain=True)
    client.publish(f"{NODE}/target", "62", retain=True)
    client.publish(f"{NODE}/current", "58.5", retain=True)
    client.publish(f"{NODE}/t5u", "61.0", retain=True)
    client.publish(f"{NODE}/t5l", "56.0", retain=True)
    client.publish(f"{NODE}/t3", "42.0", retain=True)
    client.publish(f"{NODE}/t4", "18.0", retain=True)
    client.publish(f"{NODE}/sanitize", "OFF", retain=True)
    print(f"HWS stand-in published to {host}")
    time.sleep(1)
    client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
