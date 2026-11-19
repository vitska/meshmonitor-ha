DOMAIN = "meshmonitor"
CONF_URL = "url"
CONF_TOKEN = "token"
# Safety-net poll only: live movement arrives over the socket.io push stream.
DEFAULT_SCAN_INTERVAL = 60
PLATFORMS = ["device_tracker", "sensor", "binary_sensor"]

# Live messages only change the Unified Messages sensor.  A dedicated signal
# avoids a full coordinator update (and every node entity re-render) per
# message.
SIGNAL_UNIFIED_MESSAGE = f"{DOMAIN}_unified_message"

# A node counts as "available" while it has been heard within this window.
# Nodes outside it are not exposed to Home Assistant at all.
ONLINE_WINDOW_SECONDS = 2 * 60 * 60

# Unified Messages kept on the coordinator and exposed by the sensor.
UNIFIED_MESSAGE_LIMIT = 50
UNIFIED_ATTRIBUTE_LIMIT = 20

# MeshMonitor source types -> human readable mesh network name.
SOURCE_TYPE_LABELS = {
    "meshtastic": "Meshtastic",
    "meshtastic_tcp": "Meshtastic",
    "meshtastic_serial": "Meshtastic",
    "meshtastic_mqtt": "Meshtastic",
    "meshcore": "MeshCore",
    "meshcore_mqtt": "MeshCore",
    "reticulum": "Reticulum",
    "mqtt_broker": "MQTT",
    "mqtt_bridge": "MQTT",
}


def protocol_label(source_type: str | None) -> str:
    """Map a MeshMonitor source type to a readable network name."""
    if not source_type:
        return "Unknown"
    key = str(source_type).strip().lower()
    if key in SOURCE_TYPE_LABELS:
        return SOURCE_TYPE_LABELS[key]
    for prefix, label in SOURCE_TYPE_LABELS.items():
        if key.startswith(prefix):
            return label
    return key.replace("_", " ").title()


# Hop links are cached so they survive MeshMonitor pruning its neighbour-info
# window (and HA restarts); a link is dropped once it is this old.
LINK_TTL_DAYS = 30
LINK_STORE = ".storage/meshmonitor_links.json"