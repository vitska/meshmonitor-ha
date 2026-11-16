# Changelog

All notable changes to this integration are documented here. Versions follow
[Semantic Versioning](https://semver.org/).

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
