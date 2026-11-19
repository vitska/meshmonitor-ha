"""Push updates from MeshMonitor's socket.io stream into the coordinator.

MeshMonitor publishes every node/telemetry change on its socket.io endpoint, so
the integration does not need to poll for movement: it subscribes once
(``join-all-sources``) and applies node payloads as they arrive. The regular
coordinator poll stays in place as a slow safety net.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant

from .coordinator import MeshMonitorCoordinator

_LOGGER = logging.getLogger(__name__)

NODE_EVENTS = {
    "node:updated",
    "node:batch",
    "meshcore:node:updated",
    "meshcore:local-node:updated",
}

TELEMETRY_EVENTS = {"telemetry:batch"}

MESSAGE_EVENTS = {"message:new", "meshcore:message"}

RECONNECT_MIN = 5
RECONNECT_MAX = 60


class MeshMonitorPush:
    """Keeps a socket.io subscription open and feeds the coordinator."""

    def __init__(
        self,
        hass: HomeAssistant,
        session: aiohttp.ClientSession,
        url: str,
        token: str,
        coordinator: MeshMonitorCoordinator,
    ) -> None:
        self._hass = hass
        self._session = session
        self._url = url.rstrip("/")
        self._token = token
        self._coordinator = coordinator
        self._task: asyncio.Task | None = None
        self._closing = False
        self._seen_events: set[str] = set()
        # MeshMonitor pushes node/telemetry updates in bursts.  Coalesce them
        # into at most one coordinator update per second instead of re-rendering
        # every entity for every pushed row.
        self._update_scheduled = False
        self._update_handle: asyncio.TimerHandle | None = None

    @property
    def _ws_url(self) -> str:
        secure = self._url.startswith("https://")
        host = self._url.split("://", 1)[1]
        scheme = "wss" if secure else "ws"
        return f"{scheme}://{host}/socket.io/?EIO=4&transport=websocket"

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._closing = False
        self._task = self._hass.async_create_background_task(
            self._run(), name="meshmonitor-push"
        )

    async def async_stop(self) -> None:
        self._closing = True
        if self._update_handle is not None:
            self._update_handle.cancel()
            self._update_handle = None
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def _schedule_update(self) -> None:
        """Apply pushed rows to Home Assistant at most once per second."""
        if self._closing or self._update_scheduled:
            return
        self._update_scheduled = True
        self._update_handle = self._hass.loop.call_later(1.0, self._flush_update)

    def _flush_update(self) -> None:
        self._update_scheduled = False
        self._update_handle = None
        if self._closing or self._coordinator.data is None:
            return
        self._coordinator.async_set_updated_data(self._coordinator.data)

    async def _run(self) -> None:
        backoff = RECONNECT_MIN
        while not self._closing:
            try:
                await self._consume()
                backoff = RECONNECT_MIN
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - never let the stream die
                _LOGGER.debug("MeshMonitor push stream error: %s", error)
            if self._closing:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX)

    async def _consume(self) -> None:
        async with self._session.ws_connect(self._ws_url, timeout=30) as ws:
            async for message in ws:
                if message.type is not aiohttp.WSMsgType.TEXT:
                    break
                text: str = message.data
                if text == "2":  # engine.io ping
                    await ws.send_str("3")
                    continue
                if text.startswith("0"):  # engine.io handshake
                    await ws.send_str('40{"token":"%s"}' % self._token)
                    continue
                if text.startswith("40"):  # namespace connected
                    _LOGGER.info("MeshMonitor push stream connected")
                    await ws.send_str('42["join-all-sources"]')
                    # close the gap left by the time we were disconnected
                    await self._coordinator.async_request_refresh()
                    continue
                if not text.startswith("42"):
                    continue
                try:
                    payload = json.loads(text[2:])
                except ValueError:
                    continue
                if not isinstance(payload, list) or not payload:
                    continue
                event = payload[0]
                if event not in self._seen_events:
                    self._seen_events.add(event)
                    _LOGGER.debug("MeshMonitor push event seen: %s", event)
                if len(payload) < 2 or not isinstance(payload[1], dict):
                    continue
                if event in NODE_EVENTS:
                    if event == "node:batch":
                        self._apply_batched_nodes(payload[1])
                    else:
                        self._apply(payload[1])
                elif event in TELEMETRY_EVENTS:
                    self._apply_telemetry(payload[1])
                elif event in MESSAGE_EVENTS:
                    self._apply_message(event, payload[1])

    def _nodes_of(self, source_id: str | None) -> list[dict[str, Any]]:
        data = self._coordinator.data or {}
        candidates = [source_id] if source_id in data else list(data)
        nodes: list[dict[str, Any]] = []
        for candidate in candidates:
            nodes.extend((data.get(candidate) or {}).get("nodes") or [])
        return nodes

    def _apply(self, payload: dict[str, Any]) -> None:
        node = payload.get("node")
        if not isinstance(node, dict) or not node:
            return
        source_id = payload.get("sourceId")
        node_id = node.get("nodeId")
        node_num = node.get("nodeNum") or payload.get("nodeNum")
        data = self._coordinator.data or {}
        if source_id in data:
            candidates = [source_id]
        else:
            candidates = list(data)
        for candidate in candidates:
            for existing in (data.get(candidate) or {}).get("nodes") or []:
                if (node_id and existing.get("nodeId") == node_id) or (
                    node_num and existing.get("nodeNum") == node_num
                ):
                    existing.update(node)
                    self._schedule_update()
                    return
        # A node we have never polled: fetch the full picture once.
        self._hass.async_create_task(self._coordinator.async_request_refresh())

    def _apply_batched_nodes(self, batch: dict[str, Any]) -> None:
        """node:batch carries {nodeNum: {field: value}} for several nodes."""
        changed = False
        for node_num, fields in batch.items():
            if not isinstance(fields, dict):
                continue
            for node in self._nodes_of(None):
                if str(node.get("nodeNum")) == str(node_num):
                    node.update(fields)
                    changed = True
                    break
        if changed:
            self._schedule_update()

    def _apply_message(self, event: str, data: Any) -> None:
        """Feed one live message into the unified list and the HA event bus."""
        if not isinstance(data, dict):
            return
        protocol = "MeshCore" if event.startswith("meshcore") else "Meshtastic"
        normalized = self._coordinator.add_unified_message(data, protocol=protocol)
        if normalized:
            self._hass.bus.async_fire("meshmonitor_message", normalized)

    def _apply_telemetry(self, batch: dict[str, Any]) -> None:
        """telemetry:batch carries {nodeNum: [{telemetryType, value, ...}]}."""
        changed = False
        nodes_by_num = {str(node.get("nodeNum")): node for node in self._nodes_of(None)}
        for node_num, rows in batch.items():
            node = nodes_by_num.get(str(node_num))
            if node is None or not isinstance(rows, list):
                continue
            telemetry = node.setdefault("telemetry", {})
            for row in rows:
                if not isinstance(row, dict):
                    continue
                key = row.get("telemetryType") or row.get("type")
                if not key or "value" not in row:
                    continue
                if telemetry.get(key) != row["value"]:
                    telemetry[key] = row["value"]
                    changed = True
        if changed:
            self._schedule_update()
