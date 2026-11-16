"""Services for the MeshMonitor integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .api import MeshMonitorApi, MeshMonitorError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SERVICE_SEND_TEXT = "send_text"

ATTR_TEXT = "text"
ATTR_CHANNEL = "channel"
ATTR_SOURCE = "source"

SEND_TEXT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_TEXT): cv.string,
        # Channels are addressed by name (display name as shown by MeshMonitor).
        vol.Optional(ATTR_CHANNEL): cv.string,
        # Source may be a MeshMonitor source id or its display name; omitted
        # means the primary source.
        vol.Optional(ATTR_SOURCE): cv.string,
    }
)


def _iter_sources(hass: HomeAssistant) -> list[tuple[MeshMonitorApi, dict[str, Any]]]:
    """Return (api, source metadata) for every configured source."""
    result: list[tuple[MeshMonitorApi, dict[str, Any]]] = []
    for coordinator in (hass.data.get(DOMAIN) or {}).values():
        for source_id, entry in (coordinator.data or {}).items():
            source = dict(entry.get("source") or {})
            source["id"] = source_id
            result.append((coordinator.api, source))
    return result


def _resolve_source(
    sources: list[tuple[MeshMonitorApi, dict[str, Any]]], wanted: str | None
) -> tuple[MeshMonitorApi, dict[str, Any]]:
    if not sources:
        raise HomeAssistantError("No MeshMonitor source is available")

    if wanted is None or str(wanted).strip() == "":
        for api, source in sources:
            if source.get("isPrimary") and source.get("enabled", True):
                return api, source
        for api, source in sources:
            if source.get("enabled", True):
                return api, source
        return sources[0]

    key = str(wanted).strip().lower()
    for api, source in sources:
        if str(source.get("id", "")).lower() == key:
            return api, source
        if str(source.get("name", "")).strip().lower() == key:
            return api, source

    available = ", ".join(
        sorted({str(source.get("name") or source.get("id")) for _, source in sources})
    )
    raise HomeAssistantError(f"Unknown MeshMonitor source '{wanted}'. Available: {available}")


def _channel_label(channel: dict[str, Any]) -> str:
    return str(channel.get("displayName") or channel.get("name") or channel.get("id"))


def _default_channel(channels: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick the channel used when the caller does not name one.

    Preference: an encrypted ("secure") channel, then one whose name says
    private, then the primary channel of the source.
    """
    enabled = [c for c in channels if int(c.get("role", 0)) != 0] or channels
    if not enabled:
        raise HomeAssistantError("The MeshMonitor source has no channels")

    secure = [c for c in enabled if str(c.get("encryptionStatus")) == "secure"]
    if secure:
        named = [c for c in secure if "private" in _channel_label(c).lower()]
        pool = named or secure
        return min(pool, key=lambda c: int(c.get("id", 0)))

    named = [c for c in enabled if "private" in _channel_label(c).lower()]
    if named:
        return min(named, key=lambda c: int(c.get("id", 0)))

    primary = [c for c in enabled if int(c.get("role", 0)) == 1]
    if primary:
        _LOGGER.warning(
            "No private channel found; send_text falls back to %s", _channel_label(primary[0])
        )
        return primary[0]

    return min(enabled, key=lambda c: int(c.get("id", 0)))


def _resolve_channel(channels: list[dict[str, Any]], wanted: str | None) -> dict[str, Any]:
    """Resolve a channel by name (never by number)."""
    if not channels:
        raise HomeAssistantError("The MeshMonitor source has no channels")

    if wanted is None or str(wanted).strip() == "":
        return _default_channel(channels)

    value = str(wanted).strip()
    if value.isdigit():
        raise HomeAssistantError(
            f"Channel must be a name, not a number ('{value}'). "
            f"Available: {_channel_options(channels)}"
        )

    key = value.lower()
    for channel in channels:
        if str(channel.get("displayName") or "").strip().lower() == key:
            return channel
    for channel in channels:
        if str(channel.get("name") or "").strip().lower() == key:
            return channel

    raise HomeAssistantError(
        f"Unknown channel '{wanted}'. Available: {_channel_options(channels)}"
    )


def _channel_options(channels: list[dict[str, Any]]) -> str:
    return ", ".join(sorted({_channel_label(c) for c in channels}))


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration services (once per HA instance)."""

    async def async_handle_send_text(call: ServiceCall) -> ServiceResponse:
        api, source = _resolve_source(_iter_sources(hass), call.data.get(ATTR_SOURCE))
        source_id = source["id"]

        try:
            channels = await api.async_get_channels(source_id)
        except MeshMonitorError as error:
            raise HomeAssistantError(f"Cannot read channels of '{source_id}': {error}") from error

        channel = _resolve_channel(channels, call.data.get(ATTR_CHANNEL))
        channel_index = int(channel.get("id"))

        try:
            await api.async_send_text(source_id, call.data[ATTR_TEXT], channel_index)
        except MeshMonitorError as error:
            raise HomeAssistantError(f"MeshMonitor send failed: {error}") from error

        _LOGGER.debug(
            "sent text on source %s channel %s (#%s)",
            source_id,
            _channel_label(channel),
            channel_index,
        )
        return {
            "source": source.get("name") or source_id,
            "source_id": source_id,
            "channel": _channel_label(channel),
            "channel_id": channel_index,
            "encryption": channel.get("encryptionStatus"),
            "sent": True,
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_TEXT,
        async_handle_send_text,
        schema=SEND_TEXT_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )


async def async_unload_services(hass: HomeAssistant) -> None:
    """Remove the integration services."""
    hass.services.async_remove(DOMAIN, SERVICE_SEND_TEXT)
