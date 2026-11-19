# Changelog

All notable changes to this integration are documented here. Versions follow
[Semantic Versioning](https://semver.org/).

## 0.8.1

### Fixed

- **Node visibility no longer blocks the Home Assistant event loop.** The
  sensor, device tracker and binary sensor listeners used to scan the whole
  entity registry once per mesh node on every coordinator update. With 937
  known nodes and 3,323 entities that was ~3.1 million string checks per
  platform per update (measured 4.7 s per platform on a desktop CPU, far
  longer on the ARM host). A single refresh could freeze Core for over a
  minute, so the Supervisor watchdog missed health checks and restarted Core.
  The registry is now indexed once, and only on nodes whose visibility
  actually changed (measured 26 ms for the index).
- **Live Unified Messages no longer re-render every node entity.** A live
  message now notifies only the Unified Messages sensor through a dispatcher
  signal; it used to call `async_set_updated_data`, which walked every
  coordinator entity for each message.

### Changed

- Node and telemetry rows pushed by MeshMonitor are coalesced into at most one
  coordinator update per second instead of one update per pushed row.

## 0.8.0

### Added

- Hop links per source: neighbour-info rows plus traceroute path edges, with a
  30-day persisted cache so links survive MeshMonitor pruning its neighbour
  window and Home Assistant restarts.
- MQTT source activity sensors: inbound, outbound and dropped counters for every
  MQTT broker and bridge source.
- Local-node radio health: channel utilisation, air utilisation TX, and packet
  counters (received, transmitted, bad, duplicates, relayed).
- MeshCore sources: nodes are discovered from MeshMonitor's MeshCore node list,
  and MeshCore hop links are exposed alongside the Meshtastic ones.

### Fixed

- MeshCore node payloads containing lone UTF-16 surrogates no longer abort the
  whole refresh; the JSON is sanitised per response.
- One failing source no longer stops the other sources from updating.

## 0.7.1

- MeshCore node support, telemetry counter entities, and tolerant JSON parsing.

## 0.7.0

### Added

- Unified messages sensor and `meshmonitor_message` event, fed by
  `/api/unified/messages` and the socket.io message events.
- `meshmonitor.send_text` service.

### Changed

- Nodes are exposed only while heard within 2 hours; older nodes are hidden from
  the entity registry and unhidden when heard again.
- Device trackers hide for nodes without a position.
