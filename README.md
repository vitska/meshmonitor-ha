# MeshMonitor for Home Assistant

Custom Home Assistant integration for [MeshMonitor](https://github.com/Yeraze/meshmonitor) — the monitoring web UI for Meshtastic and MeshCore networks.

MeshMonitor keeps the radio and the network knowledge; this integration brings that data into Home Assistant: every node as a device, live telemetry as sensors, positions as device trackers, hop links as an entity, unified messages as an event, and a service to broadcast text.

Data arrives over MeshMonitor's socket.io push stream, with a 60-second polling fallback, so entities move when the mesh does.

## Features

- **Every MeshMonitor source in one integration** — Meshtastic (TCP, serial, MQTT), MeshCore, MQTT broker and MQTT bridge sources.
- **Per-node sensors** — battery, voltage, SNR, RSSI, hops away, altitude, last heard.
- **Radio health of the local node** — channel utilisation, air utilisation TX, and packet counters (received, transmitted, bad, duplicates, relayed).
- **MQTT source activity** — inbound, outbound and dropped packet counters for every MQTT broker/bridge source.
- **Device trackers** for nodes that report a position.
- **Online binary sensor** — a node is considered available while it was heard within the last 2 hours; older nodes are hidden from the entity registry until they are heard again.
- **Hop links sensor** — the RF links a source has actually observed (neighbour info plus traceroute path edges), with a 30-day persisted cache so links do not disappear when MeshMonitor prunes its neighbour window.
- **Unified messages** — the latest message as a sensor, plus a `meshmonitor_message` event for automations.
- **`meshmonitor.send_text`** — broadcast text on a channel of any source.

## Requirements

- Home Assistant **2024.1** or newer.
- A running [MeshMonitor](https://github.com/Yeraze/meshmonitor) instance reachable from Home Assistant (same LAN or VPN).
- A MeshMonitor **API token**: MeshMonitor → **Settings → API tokens → Generate**. The token is displayed only once.

## Installation

### HACS (recommended)

1. Open **HACS → Integrations**.
2. Open the three-dot menu → **Custom repositories**.
3. Repository: `https://github.com/vitska/meshmonitor-ha`, category **Integration** → **Add**.
4. Install **MeshMonitor**.
5. Restart Home Assistant.

### Manual

1. Copy `custom_components/meshmonitor` into `/config/custom_components/`.
2. Restart Home Assistant.

## Configuration

**Settings → Devices & services → Add integration → MeshMonitor**

| Field | Description |
|---|---|
| **URL** | Address of the MeshMonitor UI, e.g. `http://192.168.1.50:8080`. No trailing slash. |
| **API token** | `mm_v1_…` token generated in MeshMonitor. |

One entry per MeshMonitor URL. If you rotate the token in MeshMonitor, update it in the integration options (the entry will report `invalid_auth` until then).

## Entities

Entities are created per node, and per source for source-level data. `<node>` is the node's long name.

### Per node

| Entity | Unit | Notes |
|---|---|---|
| `<node> Battery` | % | Meshtastic and MeshCore nodes |
| `<node> Voltage` | V | Battery voltage |
| `<node> SNR` | dB | Last hop SNR |
| `<node> RSSI` | dBm | Last hop RSSI |
| `<node> Hops away` | — | Hop count |
| `<node> Altitude` | m | |
| `<node> Last heard` | timestamp | |
| `<node> Online` (`binary_sensor`) | on/off | Heard within 2 hours |

Sensors that a node does not report stay unavailable; the entity is not hidden, so an unavailable value keeps its history.

### Local node only

Channel utilisation, air utilisation TX, and packet counters are read from the node MeshMonitor is connected to:

`<node> Channel utilization` (%), `<node> Air util TX` (%), `<node> Packets RX`, `<node> Packets TX`, `<node> Packets RX bad`, `<node> Packets RX dupe`, `<node> Packets relayed`.

### Per source

| Entity | Notes |
|---|---|
| `<source> Hop links` | Number of observed RF links. Attribute `links` holds the list: `from`, `to`, `snr`, `last_seen`, `protocol`. |
| `<source> MQTT inbound` / `MQTT outbound` / `MQTT dropped` | MQTT broker and bridge sources only. |
| `MeshMonitor Unified messages` | Latest unified message; attributes `messages` (last 20), `count`, `channels`, `updated`. |

## Events

`meshmonitor_message` — fired for every incoming unified message. Event data: `id`, `text`, `sender`, `channel`, `timestamp`, `source`, `protocol`.

```yaml
automation:
  - alias: Notify on MeshMonitor message
    trigger:
      - platform: event
        event_type: meshmonitor_message
    action:
      - service: notify.notify
        data:
          message: "{{ trigger.event.data.sender }}: {{ trigger.event.data.text }}"
```

## Services

### `meshmonitor.send_text`

Broadcast a text message on a channel.

| Field | Required | Description |
|---|---|---|
| `text` | yes | Message body. |
| `channel` | no | Channel name as shown in MeshMonitor (e.g. `Private`, `LongFast`). Defaults to the source's private channel. |
| `source` | no | MeshMonitor source id or name. Defaults to the primary source. |

```yaml
service: meshmonitor.send_text
data:
  text: "Garage door opened"
  channel: LongFast
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `invalid_auth` when adding the integration | Wrong or revoked token. Generate a new one in MeshMonitor and re-add. |
| `cannot_connect` | Home Assistant cannot reach the URL. Check host, port and firewall. |
| Node entities unavailable | The node has not been heard within 2 hours. They become available the next time it is heard. |
| `<source> Hop links` unavailable | That source has no neighbour data (for example an MQTT-only source), or it has not reported any links yet. |
| Messages sensor stale | The unified messages endpoint is polled every 60 s; live messages need MeshMonitor to accept the API token on `/api/unified/*` (see below). |

### Note on unified messages and API tokens

Reading `/api/unified/messages` with an API token requires the MeshMonitor build to accept `Authorization: Bearer mm_v1_…` on optional-auth routes. Older builds fall through to the anonymous user and return `403`. Everything else (nodes, telemetry, hop links) only needs the authenticated v1 API.

## Support

Bug reports and feature requests: [GitHub issues](https://github.com/vitska/meshmonitor-ha/issues).

MeshMonitor itself is a separate project: [Yeraze/meshmonitor](https://github.com/Yeraze/meshmonitor).

## License

[MIT](LICENSE)
