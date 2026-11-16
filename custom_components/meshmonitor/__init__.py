"""MeshMonitor integration entry points."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import MeshMonitorApi
from .const import CONF_TOKEN, CONF_URL, DOMAIN, PLATFORMS
from .coordinator import MeshMonitorCoordinator
from .push import MeshMonitorPush
from .services import async_setup_services, async_unload_services


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = async_get_clientsession(hass)
    api = MeshMonitorApi(session, entry.data[CONF_URL], entry.data[CONF_TOKEN])
    coordinator = MeshMonitorCoordinator(hass, api)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await async_setup_services(hass)

    push = MeshMonitorPush(
        hass,
        session,
        entry.data[CONF_URL],
        entry.data[CONF_TOKEN],
        coordinator,
    )
    coordinator.push = push
    push.start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = hass.data[DOMAIN].get(entry.entry_id)
    push = getattr(coordinator, "push", None)
    if push is not None:
        await push.async_stop()

    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            await async_unload_services(hass)
    return unloaded
