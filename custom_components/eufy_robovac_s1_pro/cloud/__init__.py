"""Optional Eufy AIOT cloud support for room/segment cleaning.

This subpackage adds an *opt-in* cloud path so the otherwise local-only S1 Pro
integration can offer room cleaning — which the device only exposes over Eufy's
AIOT MQTT channel, never the local LAN. The HTTP auth (`http.py`) and MQTT
transport (`mqtt.py`) are vendored from jeppesens/eufy-clean; the S1-Pro-focused
orchestration (`session.py`) and room decode/command (`rooms.py`) are our own,
reusing the integration's existing hand-rolled protobuf helpers.
"""
