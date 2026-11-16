"""Shared base entity: one Home Assistant device per mesh node."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, protocol_label
from .coordinator import MeshMonitorCoordinator


class MeshMonitorNodeEntity(CoordinatorEntity[MeshMonitorCoordinator]):
    """Entity bound to a single mesh node (the node is the HA device)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MeshMonitorCoordinator, source: dict, node: dict) -> None:
        super().__init__(coordinator)
        self._source_id = source.get("id")
        self._node_id = node.get("nodeId")
        self._node_key = f"{self._source_id}_{self._node_id}"
        self._source_type = source.get("type")
        self._protocol = protocol_label(self._source_type)

    def _node(self) -> dict | None:
        payload = (self.coordinator.data or {}).get(self._source_id)
        if not payload:
            return None
        for node in payload.get("nodes", []):
            if node.get("nodeId") == self._node_id:
                return node
        return None

    @property
    def available(self) -> bool:
        return super().available and self._node() is not None

    @property
    def device_info(self) -> DeviceInfo:
        node = self._node() or {}
        name = node.get("longName") or node.get("shortName") or self._node_id
        hardware = node.get("hwModel")
        model = f"{self._protocol} node"
        if hardware is not None:
            model = f"{model} (hw {hardware})"
        return DeviceInfo(
            identifiers={(DOMAIN, self._node_key)},
            name=name,
            manufacturer="MeshMonitor",
            model=model,
            model_id=self._protocol.lower(),
            sw_version=node.get("firmwareVersion"),
        )
