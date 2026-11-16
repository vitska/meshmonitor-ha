"""Shared helpers for the MeshMonitor integration."""

from __future__ import annotations

import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import ONLINE_WINDOW_SECONDS


def node_is_online(node: dict[str, Any] | None, now: float | None = None) -> bool:
    """Return True when a MeshMonitor node has been heard recently enough.

    MeshMonitor keeps stale nodes in its tables.  The integration only exposes
    nodes that are actually present on the mesh, using the same two-hour window
    as the old "Online" binary sensor.
    """
    if not isinstance(node, dict):
        return False
    if node.get("isOnline") is False:
        return False
    last_heard = node.get("lastHeard")
    if last_heard in (None, "", 0):
        return False
    try:
        value = int(last_heard)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    if value > 10_000_000_000:  # tolerate milliseconds from older payloads
        value //= 1000
    return (now if now is not None else time.time()) - value <= ONLINE_WINDOW_SECONDS


def async_set_node_entities_hidden(
    hass: HomeAssistant,
    entry: ConfigEntry,
    key: str,
    hidden: bool,
    suffixes: tuple[str, ...] | None = None,
) -> None:
    """Hide/unhide every entity of one mesh node from the HA UI.

    Hiding (rather than deleting) keeps history and lets a node come back
    without churning the entity registry.  ``suffixes`` limits the operation to
    one platform's unique-id suffixes.
    """
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        unique_id = entity.unique_id or ""
        if unique_id != key and not unique_id.startswith(f"{key}_"):
            continue
        if suffixes is not None and not any(
            unique_id.endswith(suffix) for suffix in suffixes
        ):
            continue
        if hidden:
            if entity.hidden_by is None:
                registry.async_update_entity(
                    entity.entity_id, hidden_by=er.RegistryEntryHider.INTEGRATION
                )
        elif entity.hidden_by == er.RegistryEntryHider.INTEGRATION:
            registry.async_update_entity(entity.entity_id, hidden_by=None)
