"""Binary sensor per node: recently seen on the mesh."""

from __future__ import annotations

import time

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import MeshMonitorCoordinator
from .entity import MeshMonitorNodeEntity
from .util import async_set_node_entities_hidden, node_is_online


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: MeshMonitorCoordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        entities = []
        now = time.time()
        for source_id, payload in (coordinator.data or {}).items():
            source = payload.get("source") or {}
            for node in payload.get("nodes", []):
                key = f"{source_id}_{node.get('nodeId')}"
                online = node_is_online(node, now)
                async_set_node_entities_hidden(
                    hass, entry, key, not online, ("_online",)
                )
                if not online or key in known:
                    continue
                known.add(key)
                entities.append(MeshMonitorNodeOnline(coordinator, source, node))
        if entities:
            async_add_entities(entities)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class MeshMonitorNodeOnline(MeshMonitorNodeEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: MeshMonitorCoordinator, source: dict, node: dict) -> None:
        super().__init__(coordinator, source, node)
        self._attr_name = "Online"
        self._attr_unique_id = f"{self._node_key}_online"

    @property
    def is_on(self) -> bool:
        return node_is_online(self._node())

    @property
    def extra_state_attributes(self) -> dict:
        node = self._node() or {}
        return {
            "last_heard": node.get("lastHeard"),
            "node_id": self._node_id,
            "mesh_network": self._protocol,
        }
