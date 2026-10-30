import json
from datetime import datetime

import paho.mqtt.client as mqtt

from database import update_camera_status, add_heartbeat_record
from telemetry import record_telemetry


MQTT_HOST = "127.0.0.1"
MQTT_PORT = 1883
MQTT_TOPIC_HEARTBEATS = "survcam/cameras/+/heartbeat"

mqtt_state = {
    "connected": False,
    "last_message": None,
    "last_error": None,
}


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        mqtt_state["connected"] = True
        mqtt_state["last_error"] = None
        print("MQTT connected")

        client.subscribe(MQTT_TOPIC_HEARTBEATS)
        print(f"MQTT subscribed: {MQTT_TOPIC_HEARTBEATS}")
    else:
        mqtt_state["connected"] = False
        mqtt_state["last_error"] = f"Connect failed: {reason_code}"
        print(f"MQTT connect failed: {reason_code}")


def on_disconnect(client, userdata, disconnect_flags, reason_code, properties):
    mqtt_state["connected"] = False
    mqtt_state["last_error"] = f"Disconnected: {reason_code}"
    print(f"MQTT disconnected: {reason_code}")


def on_message(client, userdata, msg):
    topic = msg.topic
    payload_text = msg.payload.decode("utf-8", errors="replace")

    mqtt_state["last_message"] = {
        "topic": topic,
        "payload": payload_text,
        "time": datetime.now().isoformat(),
    }

    print(f"MQTT message: {topic} -> {payload_text}")

    parts = topic.split("/")

    if len(parts) != 4:
        print("MQTT ignored: invalid topic format")
        return

    if parts[0] != "survcam" or parts[1] != "cameras" or parts[3] != "heartbeat":
        print("MQTT ignored: unsupported topic")
        return

    camera_id = parts[2]

    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        print("MQTT ignored: payload is not valid JSON")
        return

    status = payload.get("status", "online")
    ip_address = payload.get("ip_address")
    rssi = payload.get("rssi")
    uptime_ms = payload.get("uptime_ms")
    fw_version = payload.get("fw_version")

    updated = update_camera_status(
        camera_id=camera_id,
        status=status,
        ip_address=ip_address,
    )

    if updated:
        print(f"Camera heartbeat updated: {camera_id} -> {status}")

        # Appended (not overwritten) so rssi/uptime_ms/status can be
        # reviewed as a trend over time - e.g. to tell apart a clean
        # reboot (uptime_ms resets near zero) from a weakening signal
        # (rssi trending down) ahead of an outage.
        add_heartbeat_record(
            camera_id=camera_id,
            status=status,
            ip_address=ip_address,
            rssi=rssi,
            uptime_ms=uptime_ms,
            fw_version=fw_version,
        )

        # Extra heartbeat fields (temperature, WiFi diagnostics, reset reasons).
        # Stored separately; never raises, skips the "offline" last-will message,
        # and never stores the camera password.
        record_telemetry(camera_id, payload)
    else:
        print(f"Camera heartbeat ignored: unknown camera_id {camera_id}")


def start_mqtt_client():
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id="survcam-backend",
    )

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    # If the broker is down when the backend starts (e.g. after a server reboot),
    # keep retrying in the background instead of crashing the whole backend.
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()

    return client


def get_mqtt_status():
    if mqtt_state["connected"]:
        status = "connected"
    else:
        status = "disconnected"

    return {
        "status": status,
        "host": MQTT_HOST,
        "port": MQTT_PORT,
        "last_message": mqtt_state["last_message"],
        "last_error": mqtt_state["last_error"],
    }