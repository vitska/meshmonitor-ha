"""Config flow for MeshMonitor."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import MeshMonitorApi, MeshMonitorAuthError, MeshMonitorError
from .const import CONF_TOKEN, CONF_URL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class MeshMonitorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].strip().rstrip("/")
            token = user_input[CONF_TOKEN].strip()
            try:
                api = MeshMonitorApi(async_get_clientsession(self.hass), url, token)
                await api.async_get_sources()
            except MeshMonitorAuthError:
                errors["base"] = "invalid_auth"
            except MeshMonitorError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001 - surface unexpected failures as connect errors
                _LOGGER.exception("unexpected error validating MeshMonitor")
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(url)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"MeshMonitor ({url})",
                    data={CONF_URL: url, CONF_TOKEN: token},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_URL, default="http://meshmonitor.local:8080"): str,
                    vol.Required(CONF_TOKEN): str,
                }
            ),
            errors=errors,
        )
