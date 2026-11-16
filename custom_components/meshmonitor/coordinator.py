"""DataUpdateCoordinator that polls MeshMonitor sources and nodes."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import MeshMonitorApi, MeshMonitorError
from .const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LINK_STORE,
    LINK_TTL_DAYS,
    UNIFIED_MESSAGE_LIMIT,
    protocol_label,
)

_LOGGER = logging.getLogger(__name__)

# Device-metric counters that MeshMonitor only stores as telemetry rows.
TELEMETRY_COUNTERS = (
    "numPacketsRx",
    "numPacketsTx",
    "numPacketsRxBad",
    "numRxDupe",
    "numTxRelay",
)


def _node_ref(value) -> str | None:
    """MeshMonitor mixes numeric node numbers and '!hex' ids; normalise both."""
    if isinstance(value, str):
        text = value.strip().lower()
        if text.startswith("!") and len(text) == 9:
            try:
                int(text[1:], 16)
            except ValueError:
                return None
            return text
    try:
        return f"!{int(value) & 0xFFFFFFFF:08x}"
    except (TypeError, ValueError):
        return None


def _as_list(value) -> list:
    """MeshMonitor stores some array columns as JSON text ('[]', '[1,2]')."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return list(value) if isinstance(value, (list, tuple)) else []


def _epoch(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number // 1000 if number > 10_000_000_000 else number


def _message_text(value) -> str:
    return str(value or "").replace("\x00", "").strip()


def normalize_unified_message(
    message: dict,
    protocol: str | None = None,
    source_id: str | None = None,
) -> dict | None:
    """Normalise a MeshMonitor unified message for the sensor attributes."""
    if not isinstance(message, dict):
        return None

    text = _message_text(message.get("text") or message.get("message"))
    if not text:
        return None

    receptions = message.get("receptions")
    first = receptions[0] if isinstance(receptions, list) and receptions else {}
    if not isinstance(first, dict):
        first = {}

    source_type = first.get("sourceType") or message.get("sourceType")
    source = (
        message.get("sourceId")
        or message.get("source_id")
        or first.get("sourceId")
        or source_id
    )
    source_name = (
        message.get("sourceName")
        or first.get("sourceName")
        or source
        or "MeshMonitor"
    )
    if protocol:
        protocol_name = protocol
    elif source_type:
        protocol_name = protocol_label(source_type)
    else:
        protocol_name = "MeshMonitor"

    sender = (
        message.get("fromNodeLongName")
        or message.get("fromNodeShortName")
        or message.get("fromNodeId")
        or message.get("fromName")
        or message.get("fromPublicKey")
        or "Unknown"
    )

    timestamp = message.get("timestamp") or message.get("senderTimestamp") or 0
    try:
        timestamp = int(timestamp)
    except (TypeError, ValueError):
        timestamp = 0
    if 0 < timestamp < 10_000_000_000:
        timestamp *= 1000

    channel = message.get("channelName")
    if not channel and message.get("channel") is not None:
        channel = f"channel {message.get('channel')}"
    if not channel:
        channel = "DM"

    message_id = (
        message.get("dedupKey")
        or message.get("id")
        or (message.get("meshcoreRowRefs") or [None])[0]
        or f"{source or 'mm'}:{timestamp}:{sender}:{text[:48]}"
    )

    return {
        "id": str(message_id),
        "text": text,
        "sender": str(sender),
        "channel": str(channel),
        "timestamp": timestamp,
        "source": str(source_name),
        "source_id": str(source) if source else None,
        "protocol": str(protocol_name),
    }


def _collect_links(source_id: str, neighbors, traceroutes, now: int) -> list[dict]:
    """Normalise neighbour rows and traceroute paths into link records."""
    links: list[dict] = []

    for row in neighbors or []:
        first = _node_ref(row.get("nodeNum"))
        second = _node_ref(row.get("neighborNodeNum"))
        if not first or not second:
            continue
        seen = _epoch(row.get("timestamp")) or _epoch(row.get("lastRxTime")) or now
        links.append(
            {
                "source": source_id,
                "from": first,
                "to": second,
                "snr": row.get("snr"),
                "kind": "neighbor",
                "last_seen": seen,
            }
        )

    for row in traceroutes or []:
        route = [_node_ref(step) for step in _as_list(row.get("route"))]
        route = [step for step in route if step]
        first = _node_ref(row.get("fromNodeNum") or row.get("fromNodeId"))
        last = _node_ref(row.get("toNodeNum") or row.get("toNodeId"))
        if not first or not last:
            continue
        path = [first, *route, last]
        snr = _as_list(row.get("snrTowards"))
        seen = _epoch(row.get("timestamp")) or now
        # A direct traceroute (empty route) still proves the two nodes hear
        # each other, so record it as a single hop.
        for index in range(len(path) - 1):
            hop_snr = snr[index] if index < len(snr) else None
            links.append(
                {
                    "source": source_id,
                    "from": path[index],
                    "to": path[index + 1],
                    "snr": hop_snr,
                    "kind": "traceroute",
                    "last_seen": seen,
                }
            )
    return links


def _collect_meshcore_links(
    source_id: str,
    rows,
    key_to_node: dict[str, str],
    now: int,
    fallback_id: str | None = None,
) -> list[dict]:
    """MeshCore neighbour rows carry public keys; map them to node ids.

    The local repeater is stored with a placeholder key (`repeater`), so a
    reporter that is not in the key map falls back to that one local node.
    """
    def resolve(value) -> str | None:
        key = str(value or "").strip().lower()
        if not key:
            return None
        if key in key_to_node:
            return key_to_node[key]
        if key[:12] in key_to_node:
            return key_to_node[key[:12]]
        return None

    links: list[dict] = []
    for row in rows or []:
        first = resolve(row.get("publicKey")) or fallback_id
        second = resolve(row.get("neighborPublicKey"))
        if not first or not second:
            continue
        heard = row.get("lastHeardSecs")
        seen = now - int(heard) if isinstance(heard, (int, float)) else now
        links.append(
            {
                "source": source_id,
                "from": first,
                "to": second,
                "snr": row.get("snr"),
                "kind": "neighbor",
                "last_seen": seen,
            }
        )
    return links


class MeshMonitorCoordinator(DataUpdateCoordinator[dict]):
    def __init__(self, hass: HomeAssistant, api: MeshMonitorApi) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.api = api
        # Link history survives MeshMonitor's neighbour-info window (and HA
        # restarts) so a hop only disappears once it is genuinely stale.
        self._links: dict[tuple[str, str, str, str], dict] = {}
        self._links_loaded = False
        self._links_path = hass.config.path(LINK_STORE)
        # Unified Messages: polled for history and kept current by the push
        # stream.  The sensor reads these lists directly.
        self.unified_messages: list[dict] = []
        self.unified_channels: list[dict] = []
        self.unified_updated: int | None = None

    async def async_load_link_cache(self) -> None:
        if self._links_loaded:
            return
        self._links_loaded = True
        if not os.path.exists(self._links_path):
            return
        try:
            raw = await self.hass.async_add_executor_job(self._read_links_file)
        except (OSError, ValueError) as error:
            _LOGGER.debug("cannot read link cache: %s", error)
            return
        for link in raw:
            key = self._link_key(link)
            if key:
                self._links[key] = link
        _LOGGER.debug("restored %d hop links", len(self._links))

    def _read_links_file(self) -> list[dict]:
        with open(self._links_path, encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, list) else []

    def _write_links_file(self) -> None:
        temp = self._links_path + ".tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(list(self._links.values()), handle)
        os.replace(temp, self._links_path)

    @staticmethod
    def _link_key(link: dict) -> tuple[str, str, str, str] | None:
        source = link.get("source")
        first = link.get("from")
        second = link.get("to")
        kind = link.get("kind") or "neighbor"
        if not source or not first or not second:
            return None
        return (str(source), str(first), str(second), str(kind))

    async def _merge_links(self, source_id: str, fresh: list[dict], now: int) -> list[dict]:
        changed = False
        for link in fresh:
            key = self._link_key(link)
            if not key:
                continue
            existing = self._links.get(key)
            if existing is None or int(link.get("last_seen") or 0) >= int(existing.get("last_seen") or 0):
                if existing != link:
                    self._links[key] = link
                    changed = True

        ttl = LINK_TTL_DAYS * 86400
        stale = [key for key, link in self._links.items() if now - int(link.get("last_seen") or 0) > ttl]
        for key in stale:
            self._links.pop(key, None)
            changed = True

        if changed:
            try:
                await self.hass.async_add_executor_job(self._write_links_file)
            except OSError as error:
                _LOGGER.debug("cannot persist link cache: %s", error)

        return [link for link in self._links.values() if link.get("source") == source_id]

    async def _async_update_data(self) -> dict:
        await self.async_load_link_cache()
        try:
            sources = await self.api.async_get_sources()
        except MeshMonitorError as error:
            raise UpdateFailed(f"cannot reach MeshMonitor: {error}") from error

        now = int(time.time())
        data: dict[str, dict] = {}
        for source in sources:
            source_id = source.get("id")
            if not source_id:
                continue
            # MQTT broker/bridge sources have no radio and no nodes of their own;
            # their virtual gateway node would duplicate an existing radio node.
            # Their counters are still worth exposing, so keep the status only.
            entry = {"source": source, "nodes": [], "status": {}, "links": []}
            if str(source.get("type") or "").startswith("mqtt"):
                try:
                    entry["status"] = await self.api.async_get_source_status(source_id)
                except MeshMonitorError as error:
                    _LOGGER.debug("mqtt source %s failed: %s", source_id, error)
                data[source_id] = entry
                continue
            fresh_meshcore: list[dict] = []
            try:
                entry["nodes"] = await self.api.async_get_nodes(
                    source_id, source.get("type")
                )
                if str(source.get("type") or "").startswith("meshcore"):
                    key_to_node: dict[str, str] = {}
                    placeholder: list[str] = []
                    for node in entry["nodes"]:
                        node_id = node.get("nodeId")
                        if not node_id:
                            continue
                        key = str(node.get("publicKey") or "").strip().lower()
                        if len(key) >= 12 and all(c in "0123456789abcdef" for c in key):
                            key_to_node[key] = node_id
                            key_to_node.setdefault(key[:12], node_id)
                        else:
                            placeholder.append(node_id)
                        # MeshCore reports pack voltage in millivolts.
                        millivolts = node.get("batteryMv")
                        if node.get("voltage") is None and isinstance(millivolts, (int, float)) and millivolts:
                            node["voltage"] = round(float(millivolts) / 1000.0, 3)
                    try:
                        rows = await self.api.async_get_meshcore_neighbors(source_id)
                    except (MeshMonitorError, ValueError, TypeError) as error:
                        _LOGGER.debug("meshcore neighbours for %s failed: %s", source_id, error)
                        rows = []
                    fallback = placeholder[0] if len(placeholder) == 1 else None
                    fresh_meshcore = _collect_meshcore_links(
                        source_id, rows, key_to_node, now, fallback
                    )
                entry["status"] = await self.api.async_get_status(source_id)
            except (MeshMonitorError, ValueError, TypeError) as error:
                _LOGGER.debug("source %s failed: %s", source_id, error)
            status = entry["status"] or {}
            # v1 status names it localNodeNum; older payloads used nodeNum.
            local_num = status.get("localNodeNum") or status.get("nodeNum")
            if local_num:
                telemetry: dict[str, object] = {}
                for telemetry_type in TELEMETRY_COUNTERS:
                    try:
                        rows = await self.api.async_get_telemetry(
                            source_id, int(local_num), telemetry_type, 1
                        )
                    except (MeshMonitorError, TypeError, ValueError) as error:
                        _LOGGER.debug("telemetry %s failed: %s", telemetry_type, error)
                        continue
                    if rows and rows[0].get("value") is not None:
                        telemetry[telemetry_type] = rows[0]["value"]
                if telemetry:
                    for node in entry["nodes"]:
                        if node.get("nodeNum") == local_num:
                            node["telemetry"] = telemetry
                            break
            try:
                neighbors = await self.api.async_get_neighbor_info(source_id)
                traceroutes = await self.api.async_get_traceroutes(source_id)
                fresh = _collect_links(source_id, neighbors, traceroutes, now)
                entry["links"] = await self._merge_links(
                    source_id, fresh + fresh_meshcore, now
                )
            except MeshMonitorError as error:
                _LOGGER.debug("links for %s failed: %s", source_id, error)
                entry["links"] = await self._merge_links(source_id, fresh_meshcore, now)
            data[source_id] = entry

        try:
            messages = await self.api.async_get_unified_messages(UNIFIED_MESSAGE_LIMIT)
            fresh: list[dict] = []
            for message in messages:
                normalized = normalize_unified_message(message)
                if normalized:
                    fresh.append(normalized)
            self.unified_messages = self._merge_messages(fresh, self.unified_messages)
            self.unified_channels = await self.api.async_get_unified_channels()
            self.unified_updated = now
        except MeshMonitorError as error:
            _LOGGER.debug("unified messages failed: %s", error)
        return data

    @staticmethod
    def _merge_messages(fresh: list[dict], existing: list[dict]) -> list[dict]:
        merged: list[dict] = []
        seen: set[str] = set()
        for message in [*fresh, *existing]:
            if not isinstance(message, dict):
                continue
            key = str(message.get("id") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(message)
        merged.sort(key=lambda item: int(item.get("timestamp") or 0), reverse=True)
        return merged[:UNIFIED_MESSAGE_LIMIT]

    def add_unified_message(
        self,
        message: dict,
        protocol: str | None = None,
        source_id: str | None = None,
    ) -> dict | None:
        """Apply a live socket message immediately (no poll wait)."""
        normalized = normalize_unified_message(message, protocol, source_id)
        if normalized is None:
            return None
        self.unified_messages = self._merge_messages([normalized], self.unified_messages)
        self.unified_updated = int(time.time())
        if self.data is not None:
            self.async_set_updated_data(self.data)
        return normalized
