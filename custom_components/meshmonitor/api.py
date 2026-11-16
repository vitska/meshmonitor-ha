"""Small async client for the MeshMonitor v1 REST API."""

from __future__ import annotations

import json
import re

from typing import Any
from urllib.parse import quote

import aiohttp


class MeshMonitorError(Exception):
    """Base error for MeshMonitor API problems."""


class MeshMonitorAuthError(MeshMonitorError):
    """Raised when the API token is rejected."""


class MeshMonitorApi:
    def __init__(self, session: aiohttp.ClientSession, url: str, token: str) -> None:
        self._session = session
        self._url = url.rstrip("/")
        self._token = token

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}

    @staticmethod
    def _parse_json(text: str):
        """MeshMonitor can emit lone UTF-16 surrogates in truncated names.

        Python's json refuses them, which used to break the whole refresh, so
        drop any high surrogate that is not followed by a low one.
        """
        try:
            return json.loads(text)
        except ValueError:
            cleaned = re.sub(
                r"\\u[dD][89abAB][0-9a-fA-F]{2}(?!\\u[dD][c-fC-F][0-9a-fA-F]{2})",
                "",
                text,
            )
            return json.loads(cleaned)

    async def _get(self, path: str):
        try:
            async with self._session.get(
                f"{self._url}{path}",
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=25),
            ) as response:
                if response.status in (401, 403):
                    raise MeshMonitorAuthError(f"HTTP {response.status}")
                response.raise_for_status()
                return self._parse_json(await response.text())
        except aiohttp.ClientError as error:
            raise MeshMonitorError(str(error)) from error

    async def _post(self, path: str, payload: dict[str, Any]) -> dict:
        headers = {**self._headers, "Content-Type": "application/json"}
        try:
            async with self._session.post(
                f"{self._url}{path}",
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status in (401, 403):
                    raise MeshMonitorAuthError(f"HTTP {response.status}")
                try:
                    body = await response.json(content_type=None)
                except ValueError:
                    body = {}
                if response.status >= 400:
                    detail = ""
                    if isinstance(body, dict):
                        detail = str(body.get("message") or body.get("error") or "")
                    raise MeshMonitorError(detail or f"HTTP {response.status}")
                return body if isinstance(body, dict) else {}
        except aiohttp.ClientError as error:
            raise MeshMonitorError(str(error)) from error

    async def async_get_sources(self) -> list[dict]:
        payload = await self._get("/api/v1/sources")
        return payload.get("data") or []

    async def async_get_nodes(
        self, source_id: str, source_type: str | None = None
    ) -> list[dict]:
        """Node rows for one source.

        MeshCore peers live in their own table and are only exposed by the
        non-v1 endpoint, which returns a bare list instead of a `data` wrapper.
        """
        if str(source_type or "").startswith("meshcore"):
            payload = await self._get(f"/api/sources/{source_id}/nodes")
            return payload if isinstance(payload, list) else []
        payload = await self._get(f"/api/v1/sources/{source_id}/nodes")
        return payload.get("data") or []

    async def async_get_status(self, source_id: str) -> dict:
        payload = await self._get(f"/api/v1/sources/{source_id}/status")
        return payload.get("data") or {}

    async def async_get_channels(self, source_id: str) -> list[dict]:
        payload = await self._get(f"/api/v1/sources/{source_id}/channels")
        return payload.get("data") or []

    async def async_get_unified_channels(self) -> list[dict]:
        """Channel list from MeshMonitor's Unified Messages view."""
        payload = await self._get("/api/unified/channels")
        if isinstance(payload, list):
            return payload
        return payload.get("data") or []

    async def async_get_unified_messages(
        self, limit: int = 50, channel: str | None = None
    ) -> list[dict]:
        """Recent Unified Messages (all sources/channels, or one channel)."""
        path = f"/api/unified/messages?limit={max(1, min(int(limit), 100))}"
        if channel:
            path += f"&channel={quote(channel)}"
        payload = await self._get(path)
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            return payload.get("messages") or payload.get("data") or []
        return []

    async def async_get_neighbor_info(self, source_id: str) -> list[dict]:
        """Neighbour links heard by the mesh (Meshtastic NeighborInfo, MeshCore table)."""
        payload = await self._get(f"/api/sources/{source_id}/neighbor-info")
        return payload if isinstance(payload, list) else []

    async def async_get_traceroutes(self, source_id: str) -> list[dict]:
        """Traceroute results; entries with a route describe the hops taken."""
        payload = await self._get(f"/api/sources/{source_id}/traceroutes")
        return payload if isinstance(payload, list) else []

    async def async_get_meshcore_neighbors(self, source_id: str) -> list[dict]:
        """Neighbour rows MeshMonitor collected from MeshCore repeaters."""
        payload = await self._get(f"/api/sources/{source_id}/meshcore/neighbors")
        data = payload.get("data") if isinstance(payload, dict) else None
        items = data.get("items") if isinstance(data, dict) else None
        return items if isinstance(items, list) else []

    async def async_get_source_status(self, source_id: str) -> dict:
        """Full source status; MQTT brokers/bridges keep their counters here."""
        payload = await self._get(f"/api/sources/{source_id}/status")
        return payload if isinstance(payload, dict) else {}

    async def async_get_telemetry(
        self,
        source_id: str,
        node_num: int,
        telemetry_type: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        """Newest telemetry rows for one node (packet counters live here)."""
        path = f"/api/v1/sources/{source_id}/telemetry?nodeNum={node_num}&limit={limit}"
        if telemetry_type:
            path += f"&type={telemetry_type}"
        payload = await self._get(path)
        rows = payload.get("data") if isinstance(payload, dict) else None
        return rows if isinstance(rows, list) else []

    async def async_send_text(
        self, source_id: str, text: str, channel_index: int | None = None
    ) -> dict:
        """Broadcast text on a channel of a MeshMonitor source."""
        payload: dict[str, Any] = {"text": text}
        if channel_index is not None:
            payload["channel"] = channel_index
        return await self._post(f"/api/v1/sources/{source_id}/messages", payload)
