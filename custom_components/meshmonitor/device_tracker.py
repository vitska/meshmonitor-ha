"""Device tracker: node position, attached to the node's device."""

from __future__ import annotations

import math
import time

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import MeshMonitorCoordinator
from .entity import MeshMonitorNodeEntity
from .util import async_apply_node_visibility, node_is_online


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: MeshMonitorCoordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[str] = set()
    visibility: dict[str, bool] = {}

    @callback
    def _add_new() -> None:
        entities = []
        now = time.time()
        changed_nodes: dict[str, bool] = {}
        for source_id, payload in (coordinator.data or {}).items():
            source = payload.get("source") or {}
            for node in payload.get("nodes", []):
                key = f"{source_id}_{node.get('nodeId')}"
                visible = (
                    node_is_online(node, now)
                    and node.get("latitude") is not None
                    and node.get("longitude") is not None
                )
                if visibility.get(key) != visible:
                    visibility[key] = visible
                    changed_nodes[key] = visible
                if not visible or key in known:
                    continue
                known.add(key)
                entities.append(MeshMonitorTracker(coordinator, source, node))
        if changed_nodes:
            async_apply_node_visibility(hass, entry, changed_nodes, ("_position",))
        if entities:
            async_add_entities(entities)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class MeshMonitorTracker(MeshMonitorNodeEntity, TrackerEntity):
    def __init__(self, coordinator: MeshMonitorCoordinator, source: dict, node: dict) -> None:
        super().__init__(coordinator, source, node)
        self._attr_name = None  # entity takes the device name
        self._attr_unique_id = f"{self._node_key}_position"

    @property
    def latitude(self) -> float | None:
        node = self._node()
        return node.get("latitude") if node else None

    @property
    def longitude(self) -> float | None:
        node = self._node()
        return node.get("longitude") if node else None

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def location_accuracy(self) -> int:
        node = self._node() or {}
        return int(node.get("positionGpsAccuracy") or 0)

    @property
    def location_name(self) -> str:
        latitude = self.latitude
        longitude = self.longitude
        zone = self.hass.states.get("zone.home")
        if latitude is not None and longitude is not None and zone is not None:
            zone_lat = zone.attributes.get("latitude")
            zone_lon = zone.attributes.get("longitude")
            radius = zone.attributes.get("radius", 100)
            if zone_lat is not None and zone_lon is not None:
                if _distance_m(latitude, longitude, zone_lat, zone_lon) <= radius:
                    return "home"
        return "not_home"

    @property
    def extra_state_attributes(self) -> dict:
        node = self._node() or {}
        return {
            "node_id": self._node_id,
            "mesh_network": self._protocol,
            "altitude": node.get("altitude"),
            "snr": node.get("snr"),
            "rssi": node.get("rssi"),
            "hops_away": node.get("hopsAway"),
            "battery_level": node.get("batteryLevel"),
            "last_heard": node.get("lastHeard"),
        }
