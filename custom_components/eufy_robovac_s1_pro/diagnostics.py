"""Diagnostics support for Eufy RoboVac S1 Pro.

Adds a "Download Diagnostics" button on the integration page that dumps
all current DPS values, device info, coordinator state, and parsed protobuf
data as a downloadable JSON file.
"""

from __future__ import annotations

import base64
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_COORDINATOR, CONF_DISCOVERED_DEVICES, DOMAIN
from .protobuf_parser import (
    parse_dps164,
    parse_dps167,
    parse_dps168,
)

# Human-readable labels for all known DPS keys
DPS_LABELS: dict[str, str] = {
    "1": "Play/Pause (legacy)",
    "2": "Play/Pause",
    "3": "Direction Control",
    "5": "Clean Mode",
    "6": "Status Indicator 1",
    "7": "Status Indicator 2",
    "8": "Battery Level",
    "9": "Fan Speed (primary)",
    "10": "Mop/Water Level",
    "40": "Mop Pad Status",
    "101": "Go Home",
    "102": "Speed (legacy)",
    "103": "Find Vacuum",
    "104": "Electric Status",
    "105": "Mop Mode",
    "106": "Error Alarm",
    "107": "Do Not Disturb Schedule",
    "108": "Position Data",
    "109": "Last Clean Time (legacy)",
    "110": "Last Clean Area (legacy)",
    "111": "Volume",
    "112": "Side Brush Life (legacy)",
    "113": "Main Brush Life (legacy)",
    "114": "Filter Life (legacy)",
    "115": "Maintenance Reset",
    "116": "Area Clean Command",
    "117": "Area Clean Active",
    "118": "Boost IQ",
    "119": "Total Clean Time (legacy)",
    "120": "Total Clean Area (legacy)",
    "121": "Map/Path Data",
    "122": "Pause/Start",
    "123": "Language",
    "124": "Area Set",
    "125": "Voice Type",
    "126": "Settings",
    "127": "Sensor Life (legacy)",
    "128": "Voice Default Set",
    "129": "Mop (alt)",
    "130": "Speed (alt)",
    "131": "Remote Control",
    "137": "Remind Alarm",
    "139": "Do Not Disturb Toggle",
    "140": "Smart Rooms",
    "141": "Edit Room",
    "142": "Log Upload",
    "143": "Map Manager",
    "144": "Mopping Water Level",
    "145": "Schedule Mopping Water",
    "146": "Map Manage",
    "147": "Save Map / Collect Dust",
    "152": "Command (S1 Pro)",
    "153": "Status (S1 Pro, protobuf)",
    "154": "Cleaning Mode Config (S1 Pro, protobuf)",
    "156": "Auto-Return Cleaning",
    "158": "Fan Speed (S1 Pro display)",
    "159": "Boost IQ",
    "161": "Unknown Numeric (100 when docked)",
    "163": "Battery Level (alt)",
    "164": "Room Definitions (protobuf)",
    "167": "Cleaning Statistics (protobuf)",
    "168": "Consumable Usage (protobuf)",
    "173": "Schedule/Config Data (protobuf)",
    "178": "Cleaning History (protobuf)",
}


def _redact_sensitive(data: dict) -> dict:
    """Redact sensitive fields from config entry data."""
    redacted = dict(data)
    for key in ("password", "local_key", "LOCAL_KEY"):
        if key in redacted:
            redacted[key] = "**REDACTED**"
    return redacted


def _format_dps_value(key: str, value: Any) -> dict:
    """Format a single DPS value with extra debug info."""
    info: dict[str, Any] = {
        "raw": value,
        "label": DPS_LABELS.get(key, "Unknown"),
    }

    if isinstance(value, str) and len(value) >= 4:
        try:
            decoded = base64.b64decode(value)
            info["base64_decoded_hex"] = " ".join(f"{b:02x}" for b in decoded)
            info["base64_decoded_length"] = len(decoded)
        except Exception:
            pass

    return info


def _parse_protobuf_data(raw_dps: dict) -> dict[str, Any]:
    """Parse protobuf-encoded DPS values into human-readable structures."""
    parsed: dict[str, Any] = {}

    # DPS 167: Cleaning Statistics
    dps167 = raw_dps.get("167", "")
    if dps167:
        stats = parse_dps167(dps167)
        if stats:
            entry: dict[str, Any] = {
                "total_time_seconds": stats.total_time_seconds,
                "total_time_hours": round(stats.total_time_seconds / 3600, 1),
                "total_area_sqm": stats.total_area_sqm,
                "total_count": stats.total_count,
            }
            if stats.last_clean:
                entry["last_clean"] = {
                    "time_seconds": stats.last_clean.time_seconds,
                    "area_sqm": stats.last_clean.area_sqm,
                }
            else:
                entry["last_clean"] = None
            parsed["dps_167_cleaning_statistics"] = entry

    # DPS 168: Consumable Usage
    dps168 = raw_dps.get("168", "")
    if dps168:
        consumables = parse_dps168(dps168)
        if consumables:
            parsed["dps_168_consumable_usage"] = [
                {
                    "name": c.name,
                    "field_id": c.field_id,
                    "raw_value": c.raw_value,
                    "max_value": c.max_value,
                    "life_remaining_pct": c.life_remaining_pct,
                }
                for c in consumables
            ]

    # DPS 164: Room Definitions
    dps164 = raw_dps.get("164", "")
    if dps164:
        rooms = parse_dps164(dps164)
        if rooms:
            parsed["dps_164_room_definitions"] = [
                {
                    "room_id": r.room_id,
                    "room_type": r.room_type,
                }
                for r in rooms
            ]

    return parsed


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

        # Format each DPS value with labels and base64 decoding
        dps_detailed: dict[str, Any] = {}
        for dps_key, dps_val in sorted(raw_dps.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 0):
            dps_detailed[dps_key] = _format_dps_value(dps_key, dps_val)

        # Parse protobuf blobs into structured data
        parsed_protobuf = _parse_protobuf_data(raw_dps)

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
            "parsed_protobuf": parsed_protobuf,
            "dps_raw": raw_dps,
        }

        diagnostics["devices"][device_id] = device_info

    return diagnostics
