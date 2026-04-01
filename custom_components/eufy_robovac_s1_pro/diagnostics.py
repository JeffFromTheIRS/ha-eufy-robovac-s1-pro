"""Diagnostics support for Eufy RoboVac S1 Pro.

Adds a "Download Diagnostics" button on the integration page that dumps
all current DPS values, device info, and coordinator state as JSON.
"""

from __future__ import annotations

import base64
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_COORDINATOR, CONF_DISCOVERED_DEVICES, DOMAIN


def _redact_sensitive(data: dict) -> dict:
    """Redact sensitive fields from config entry data."""
    redacted = dict(data)
    for key in ("password", "local_key", "LOCAL_KEY"):
        if key in redacted:
            redacted[key] = "**REDACTED**"
    return redacted


def _format_dps_value(key: str, value: Any) -> dict:
    """Format a single DPS value with extra debug info for base64 data."""
    info: dict[str, Any] = {"raw": value}

    if isinstance(value, str) and len(value) >= 4:
        try:
            decoded = base64.b64decode(value)
            info["base64_decoded_hex"] = " ".join(f"{b:02x}" for b in decoded)
            info["base64_decoded_length"] = len(decoded)
        except Exception:
            pass

    return info


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    diagnostics: dict[str, Any] = {
        "config_entry": {
            "entry_id": entry.entry_id,
            "title": entry.title,
            "data": _redact_sensitive(dict(entry.data)),
        },
        "devices": {},
    }

    domain_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    discovered = domain_data.get(CONF_DISCOVERED_DEVICES, {})

    for device_id, props in discovered.items():
        coordinator = props.get(CONF_COORDINATOR)
        if not coordinator:
            diagnostics["devices"][device_id] = {"error": "no coordinator"}
            continue

        raw_dps = dict(coordinator.data) if coordinator.data else {}

        # Categorize DPS values
        dps_detailed: dict[str, Any] = {}
        for dps_key, dps_val in sorted(raw_dps.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 0):
            dps_detailed[dps_key] = _format_dps_value(dps_key, dps_val)

        device_info: dict[str, Any] = {
            "tuya_device_id": coordinator.tuya_client.device_id,
            "host": coordinator.tuya_client.host,
            "mac": coordinator.mac,
            "coordinator_name": coordinator.name,
            "update_interval_seconds": coordinator.update_interval.total_seconds() if coordinator.update_interval else None,
            "last_update_success": coordinator.last_update_success,
            "dps_count": len(raw_dps),
            "dps_keys_present": sorted(raw_dps.keys(), key=lambda x: int(x) if x.isdigit() else 0),
            "dps_values": dps_detailed,
            "dps_raw": raw_dps,
        }

        diagnostics["devices"][device_id] = device_info

    return diagnostics
