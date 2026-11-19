"""Sensors per mesh node: battery, voltage, SNR, RSSI, hops, altitude, last heard."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SIGNAL_UNIFIED_MESSAGE, UNIFIED_ATTRIBUTE_LIMIT
from .coordinator import MeshMonitorCoordinator
from .entity import MeshMonitorNodeEntity
from .util import async_apply_node_visibility, node_is_online


def _timestamp(node: dict):
    value = node.get("lastHeard")
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _telemetry(key: str):
    """Read a device-telemetry value pushed by MeshMonitor's telemetry:batch."""

    def reader(node: dict):
        return (node.get("telemetry") or {}).get(key)

    return reader


SENSORS = [
    {
        "key": "battery",
        "name": "Battery",
        "unit": "%",
        "device_class": SensorDeviceClass.BATTERY,
        "state_class": SensorStateClass.MEASUREMENT,
        "icon": None,
        "value": lambda node: node.get("batteryLevel"),
    },
    {
        "key": "voltage",
        "name": "Voltage",
        "unit": "V",
        "device_class": SensorDeviceClass.VOLTAGE,
        "state_class": SensorStateClass.MEASUREMENT,
        "icon": None,
        "value": lambda node: node.get("voltage"),
    },
    {
        "key": "snr",
        "name": "SNR",
        "unit": "dB",
        "device_class": None,
        "state_class": SensorStateClass.MEASUREMENT,
        "icon": "mdi:signal",
        "value": lambda node: node.get("snr"),
    },
    {
        "key": "rssi",
        "name": "RSSI",
        "unit": "dBm",
        "device_class": SensorDeviceClass.SIGNAL_STRENGTH,
        "state_class": SensorStateClass.MEASUREMENT,
        "icon": None,
        "value": lambda node: node.get("rssi"),
    },
    {
        "key": "hops",
        "name": "Hops away",
        "unit": None,
        "device_class": None,
        "state_class": None,
        "icon": "mdi:transit-connection-variant",
        "value": lambda node: node.get("hopsAway"),
    },
    {
        "key": "altitude",
        "name": "Altitude",
        "unit": "m",
        "device_class": SensorDeviceClass.DISTANCE,
        "state_class": SensorStateClass.MEASUREMENT,
        "icon": None,
        "value": lambda node: node.get("altitude"),
    },
    {
        "key": "last_heard",
        "name": "Last heard",
        "unit": None,
        "device_class": SensorDeviceClass.TIMESTAMP,
        "state_class": None,
        "icon": None,
        "value": _timestamp,
    },
    {
        "key": "channel_utilization",
        "name": "Channel utilization",
        "unit": "%",
        "device_class": None,
        "state_class": SensorStateClass.MEASUREMENT,
        "icon": "mdi:chart-bell-curve-cumulative",
        "value": lambda node: node.get("channelUtilization"),
    },
    {
        "key": "air_util_tx",
        "name": "Air util TX",
        "unit": "%",
        "device_class": None,
        "state_class": SensorStateClass.MEASUREMENT,
        "icon": "mdi:radio-tower",
        "value": lambda node: node.get("airUtilTx"),
    },
    {
        "key": "packets_rx",
        "name": "Packets received",
        "unit": None,
        "device_class": None,
        "state_class": SensorStateClass.TOTAL_INCREASING,
        "icon": "mdi:download-network",
        "value": _telemetry("numPacketsRx"),
    },
    {
        "key": "packets_tx",
        "name": "Packets sent",
        "unit": None,
        "device_class": None,
        "state_class": SensorStateClass.TOTAL_INCREASING,
        "icon": "mdi:upload-network",
        "value": _telemetry("numPacketsTx"),
    },
    {
        "key": "packets_rx_bad",
        "name": "Packets corrupted",
        "unit": None,
        "device_class": None,
        "state_class": SensorStateClass.TOTAL_INCREASING,
        "icon": "mdi:alert-decagram-outline",
        "value": _telemetry("numPacketsRxBad"),
    },
    {
        "key": "packets_rx_dupe",
        "name": "Packets duplicate",
        "unit": None,
        "device_class": None,
        "state_class": SensorStateClass.TOTAL_INCREASING,
        "icon": "mdi:content-duplicate",
        "value": _telemetry("numRxDupe"),
    },
    {
        "key": "packets_relayed",
        "name": "Packets relayed",
        "unit": None,
        "device_class": None,
        "state_class": SensorStateClass.TOTAL_INCREASING,
        "icon": "mdi:transit-connection-variant",
        "value": _telemetry("numTxRelay"),
    },
]

SENSOR_SUFFIXES = tuple(f"_{description['key']}" for description in SENSORS)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: MeshMonitorCoordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[str] = set()
    known_sources: set[str] = set()
    visibility: dict[str, bool] = {}

    async_add_entities([MeshMonitorUnifiedMessagesSensor(coordinator, entry)])

    @callback
    def _add_new() -> None:
        entities = []
        now = time.time()
        changed_nodes: dict[str, bool] = {}
        for source_id, payload in (coordinator.data or {}).items():
            source = payload.get("source") or {}
            is_mqtt = str(source.get("type") or "").startswith("mqtt")
            is_meshcore = str(source.get("type") or "").startswith("meshcore")
            if source_id not in known_sources and not is_mqtt:
                known_sources.add(source_id)
                entities.append(MeshMonitorLinksSensor(coordinator, source))
            status = payload.get("status") or {}
            if is_mqtt:
                for mqtt_key in MQTT_SENSOR_KEYS:
                    unique = f"{source_id}_{mqtt_key}"
                    if unique in known:
                        continue
                    known.add(unique)
                    entities.append(MeshMonitorMqttSensor(coordinator, source, mqtt_key))
                continue
            local_num = status.get("localNodeNum") or status.get("nodeNum")
            for node in payload.get("nodes", []):
                key = f"{source_id}_{node.get('nodeId')}"
                online = node_is_online(node, now)
                if visibility.get(key) != online:
                    visibility[key] = online
                    changed_nodes[key] = online
                if not online:
                    continue
                if key in known:
                    continue
                known.add(key)
                # Packet counters are only reported by the radio itself, not by
                # every node it hears, so keep those entities on the local node.
                is_local = local_num is not None and node.get("nodeNum") == local_num
                for description in SENSORS:
                    if description["key"].startswith("packets_") and not is_local:
                        continue
                    # Channel/air utilisation are Meshtastic device metrics.
                    if is_meshcore and description["key"] in MESHCORE_SKIP:
                        continue
                    entities.append(MeshMonitorNodeSensor(coordinator, source, node, description))
        if changed_nodes:
            async_apply_node_visibility(hass, entry, changed_nodes, SENSOR_SUFFIXES)
        if entities:
            async_add_entities(entities)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


MESHCORE_SKIP = {"channel_utilization", "air_util_tx"}
MQTT_SENSOR_KEYS = ("mqtt_in", "mqtt_out", "mqtt_dropped")
MQTT_SENSOR_LABELS = {
    "mqtt_in": ("MQTT inbound", "mdi:download-network-outline"),
    "mqtt_out": ("MQTT outbound", "mdi:upload-network-outline"),
    "mqtt_dropped": ("MQTT dropped", "mdi:alert-circle-outline"),
}


class MeshMonitorMqttSensor(CoordinatorEntity[MeshMonitorCoordinator], SensorEntity):
    """Activity counters of one MQTT broker or bridge source."""

    _attr_has_entity_name = False
    _attr_should_poll = False
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(
        self, coordinator: MeshMonitorCoordinator, source: dict, key: str
    ) -> None:
        super().__init__(coordinator)
        self._source_id = source.get("id")
        self._source_name = source.get("name") or self._source_id
        self._key = key
        label, icon = MQTT_SENSOR_LABELS[key]
        self._attr_name = f"{self._source_name} {label}"
        self._attr_icon = icon
        self._attr_unique_id = f"{self._source_id}_{key}"

    def _status(self) -> dict:
        payload = (self.coordinator.data or {}).get(self._source_id) or {}
        status = payload.get("status") or {}
        return status if isinstance(status, dict) else {}

    def _sum(self, status: dict, key: str) -> int:
        value = status.get(key)
        if isinstance(value, dict):
            return sum(int(v) for v in value.values() if isinstance(v, int))
        return int(value) if isinstance(value, int) else 0

    @property
    def native_value(self):
        status = self._status()
        if self._key == "mqtt_in":
            return status.get("downlinkIn", status.get("packetsIn", 0))
        if self._key == "mqtt_out":
            return status.get("uplinkOut", status.get("packetsIngested", 0))
        dropped = 0
        for key in (
            "uplinkOkToMqttDrops",
            "uplinkAutomationDrops",
            "uplinkBridgeRelayDrops",
            "uplinkDisconnectedDrops",
            "uplinkAuthStoppedDrops",
            "packetsDropped",
        ):
            dropped += self._sum(status, key)
        for key in ("downlinkDrops", "uplinkDrops"):
            dropped += self._sum(status, key)
        return dropped

    @property
    def extra_state_attributes(self):
        status = self._status()
        attributes = {
            "connected": status.get("connected"),
            "mode": status.get("mode"),
            "upstream_connected": status.get("upstreamConnected"),
        }
        for key in (
            "downlinkIn",
            "downlinkIngested",
            "downlinkRepublished",
            "uplinkOut",
            "uplinkOkToMqttDrops",
            "packetsIn",
            "packetsIngested",
            "packetsDropped",
            "clientCount",
        ):
            if key in status:
                attributes[key] = status[key]
        return attributes

    @property
    def available(self) -> bool:
        return super().available and bool(self._status())

class MeshMonitorLinksSensor(CoordinatorEntity[MeshMonitorCoordinator], SensorEntity):
    """Hop links for one source: neighbour rows plus traceroute path edges.

    The state is the number of edges; the ``links`` attribute carries them as
    ``{from, to, snr, kind}`` with ``!hex`` node ids, which is what the map card
    turns into lines between node positions.
    """

    _attr_has_entity_name = False
    _attr_icon = "mdi:transit-connection-variant"
    _attr_should_poll = False

    def __init__(self, coordinator: MeshMonitorCoordinator, source: dict) -> None:
        super().__init__(coordinator)
        self._source_id = source.get("id")
        self._source_name = source.get("name") or self._source_id
        self._attr_name = f"{self._source_name} hop links"
        self._attr_unique_id = f"{self._source_id}_hop_links"

    def _links(self) -> list[dict]:
        payload = (self.coordinator.data or {}).get(self._source_id) or {}
        return payload.get("links") or []

    @property
    def native_value(self):
        return len(self._links())

    @property
    def extra_state_attributes(self):
        return {"links": self._links()}

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, str(self._source_id))},
            name=f"MeshMonitor {self._source_name}",
            manufacturer="MeshMonitor",
            model="MeshMonitor source",
            model_id=str(self._source_id),
        )


class MeshMonitorNodeSensor(MeshMonitorNodeEntity, SensorEntity):
    def __init__(
        self,
        coordinator: MeshMonitorCoordinator,
        source: dict,
        node: dict,
        description: dict,
    ) -> None:
        super().__init__(coordinator, source, node)
        self._description = description
        self._attr_name = description["name"]
        self._attr_unique_id = f"{self._node_key}_{description['key']}"
        self._attr_native_unit_of_measurement = description["unit"]
        self._attr_device_class = description["device_class"]
        self._attr_state_class = description["state_class"]
        if description["icon"]:
            self._attr_icon = description["icon"]

    @property
    def native_value(self):
        node = self._node()
        if node is None:
            return None
        return self._description["value"](node)

class MeshMonitorUnifiedMessagesSensor(CoordinatorEntity[MeshMonitorCoordinator], SensorEntity):
    """Recent Unified Messages across every MeshMonitor source.

    State is the latest message text; the ``messages`` attribute carries the
    normalised list for cards/automations.
    """

    _attr_has_entity_name = False
    _attr_icon = "mdi:message-text"
    _attr_should_poll = False

    def __init__(self, coordinator: MeshMonitorCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry_id = entry.entry_id
        self._attr_name = "Unified messages"
        self._attr_unique_id = f"{entry.entry_id}_unified_messages"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_UNIFIED_MESSAGE, self._handle_unified_message
            )
        )

    @callback
    def _handle_unified_message(self) -> None:
        """A live message changed only this sensor, not the node data."""
        self.async_write_ha_state()

    @property
    def native_value(self):
        messages = self.coordinator.unified_messages
        if not messages:
            return None
        text = str(messages[0].get("text") or "")
        return text[:255]

    @property
    def extra_state_attributes(self):
        messages = self.coordinator.unified_messages[:UNIFIED_ATTRIBUTE_LIMIT]
        payload = []
        for message in messages:
            payload.append(
                {
                    "id": message.get("id"),
                    "text": str(message.get("text") or "")[:500],
                    "sender": message.get("sender"),
                    "channel": message.get("channel"),
                    "timestamp": message.get("timestamp"),
                    "source": message.get("source"),
                    "protocol": message.get("protocol"),
                }
            )
        channels = [
            channel.get("name")
            for channel in self.coordinator.unified_channels
            if isinstance(channel, dict) and channel.get("name")
        ]
        return {
            "messages": payload,
            "count": len(self.coordinator.unified_messages),
            "channels": channels,
            "updated": self.coordinator.unified_updated,
        }

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"entry_{self._entry_id}")},
            name="MeshMonitor",
            manufacturer="MeshMonitor",
            model="MeshMonitor integration",
        )
