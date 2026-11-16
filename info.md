# MeshMonitor

Home Assistant custom integration for [MeshMonitor](https://github.com/Yeraze/meshmonitor): Meshtastic and MeshCore nodes as devices, live telemetry sensors, device trackers, hop links, unified messages and a `send_text` service.

- Live over socket.io, 60 s polling fallback.
- Meshtastic (TCP/serial/MQTT), MeshCore and MQTT sources from one MeshMonitor instance.
- Requires a MeshMonitor API token (**Settings → API tokens**).

Install through HACS as a custom repository (category **Integration**), or copy `custom_components/meshmonitor` to `/config/custom_components/`. See the [README](https://github.com/vitska/meshmonitor-ha) for the full entity and service reference.
