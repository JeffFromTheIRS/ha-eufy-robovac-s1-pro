"""Lightweight protobuf wire format decoder for Eufy RoboVac S1 Pro.

Zero-dependency decoder for the protobuf-encoded DPS blobs (164, 167, 168).
No .proto schema needed — decodes raw wire format into Python dicts.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field

_LOGGER = logging.getLogger(__name__)

# Wire types
WIRE_VARINT = 0
WIRE_FIXED64 = 1
WIRE_LEN = 2
WIRE_FIXED32 = 5


# ─── Generic protobuf decoder ────────────────────────────────────────────────


def decode_varint(data: bytes, pos: int) -> tuple[int, int]:
    """Decode a varint at the given position.

    Returns (value, next_position).
    """
    value = 0
    shift = 0
    while pos < len(data):
        byte = data[pos]
        value |= (byte & 0x7F) << shift
        pos += 1
        if not (byte & 0x80):
            break
        shift += 7
    return value, pos


def decode_field(data: bytes, pos: int) -> tuple[int, int, object, int]:
    """Decode one protobuf field.

    Returns (field_number, wire_type, value, next_position).
    - WIRE_VARINT: value is int
    - WIRE_LEN: value is bytes (sub-message or raw bytes)
    - WIRE_FIXED32: value is bytes (4 bytes)
    - WIRE_FIXED64: value is bytes (8 bytes)
    """
    tag, pos = decode_varint(data, pos)
    wire_type = tag & 0x07
    field_number = tag >> 3

    if wire_type == WIRE_VARINT:
        value, pos = decode_varint(data, pos)
    elif wire_type == WIRE_LEN:
        length, pos = decode_varint(data, pos)
        value = data[pos : pos + length]
        pos += length
    elif wire_type == WIRE_FIXED64:
        value = data[pos : pos + 8]
        pos += 8
    elif wire_type == WIRE_FIXED32:
        value = data[pos : pos + 4]
        pos += 4
    else:
        # Unknown wire type — skip (shouldn't happen in valid data)
        _LOGGER.warning("Unknown wire type %d at position %d", wire_type, pos)
        value = None

    return field_number, wire_type, value, pos


def decode_message(data: bytes) -> dict[int, list]:
    """Decode all fields from a protobuf message.

    Returns {field_number: [value1, value2, ...]} to handle repeated fields.
    """
    fields: dict[int, list] = {}
    pos = 0
    while pos < len(data):
        try:
            field_number, wire_type, value, pos = decode_field(data, pos)
            fields.setdefault(field_number, []).append(value)
        except (IndexError, ValueError) as e:
            _LOGGER.debug("Protobuf decode error at pos %d: %s", pos, e)
            break
    return fields


def strip_length_prefix(data: bytes) -> bytes:
    """Strip a leading varint length prefix from protobuf data."""
    if not data:
        return data
    length, pos = decode_varint(data, 0)
    return data[pos : pos + length]


def get_varint(fields: dict[int, list], field_num: int, default: int = 0) -> int:
    """Get a varint value from decoded fields, with a default."""
    values = fields.get(field_num, [])
    if values and isinstance(values[0], int):
        return values[0]
    return default


def get_bytes(fields: dict[int, list], field_num: int) -> bytes | None:
    """Get a bytes value from decoded fields."""
    values = fields.get(field_num, [])
    if values and isinstance(values[0], bytes):
        return values[0]
    return None


# ─── DPS 167: Cleaning Statistics ─────────────────────────────────────────────


@dataclass
class CleaningSession:
    """A single cleaning session's stats."""
    time_seconds: int = 0
    area_sqm: int = 0


@dataclass
class CleaningStatistics:
    """Parsed DPS 167 cleaning statistics."""
    last_clean: CleaningSession | None = None
    total_time_seconds: int = 0
    total_area_sqm: int = 0
    total_count: int = 0


def parse_dps167(b64_value: str) -> CleaningStatistics | None:
    """Parse DPS 167 cleaning statistics from base64-encoded protobuf.

    Structure (length-prefixed):
      Field 1 (LEN): Last clean sub-message
        - Sub-field 1 (varint): time in seconds
        - Sub-field 2 (varint): area in m²
      Field 2 (LEN): Totals sub-message
        - Sub-field 1 (varint): total time in seconds
        - Sub-field 2 (varint): total area in m²
        - Sub-field 3 (varint): total count
    """
    try:
        raw = base64.b64decode(b64_value)
        data = strip_length_prefix(raw)
        outer = decode_message(data)

        stats = CleaningStatistics()

        # Field 1: last clean session
        last_clean_bytes = get_bytes(outer, 1)
        if last_clean_bytes and len(last_clean_bytes) > 0:
            last_fields = decode_message(last_clean_bytes)
            stats.last_clean = CleaningSession(
                time_seconds=get_varint(last_fields, 1),
                area_sqm=get_varint(last_fields, 2),
            )

        # Field 2: totals
        totals_bytes = get_bytes(outer, 2)
        if totals_bytes:
            totals_fields = decode_message(totals_bytes)
            stats.total_time_seconds = get_varint(totals_fields, 1)
            stats.total_area_sqm = get_varint(totals_fields, 2)
            stats.total_count = get_varint(totals_fields, 3)

        return stats

    except Exception as e:
        _LOGGER.debug("Error parsing DPS 167: %s", e)
        return None


# ─── DPS 168: Consumable Usage Counters ───────────────────────────────────────


# Map protobuf field numbers to consumable names
CONSUMABLE_FIELD_MAP: dict[int, str] = {
    1: "side_brush",
    2: "main_brush",
    3: "filter",
    5: "sensor",
    6: "mop_pad",
    11: "mop_pad_alt",
    41: "other_1",
    43: "other_2",
}

# Rated maximum usage values for calculating remaining life %.
# These are in the same unit as the raw counter from DPS 168.
# Based on typical Eufy S1 Pro consumable lifetimes, assuming counters are in minutes:
#   Side brush: ~200 hours = 12,000 min
#   Main brush: ~300 hours = 18,000 min
#   Filter: ~200 hours = 12,000 min
#   Sensor: ~300 hours = 18,000 min
#   Mop pad: ~100 uses (different metric — may not apply)
# NOTE: These are estimates and may need tuning based on real-world data.
CONSUMABLE_MAX_VALUES: dict[str, int] = {
    "side_brush": 18_000,
    "main_brush": 18_000,
    "filter": 18_000,
    "sensor": 18_000,
    "mop_pad": 12_000,
    "mop_pad_alt": 12_000,
}


@dataclass
class ConsumableUsage:
    """A single consumable's usage data."""
    name: str
    field_id: int
    raw_value: int
    max_value: int | None = None
    life_remaining_pct: float | None = None


def parse_dps168(b64_value: str) -> list[ConsumableUsage]:
    """Parse DPS 168 consumable usage counters from base64-encoded protobuf.

    Structure (length-prefixed):
      Field 1 (LEN): Container
        Field N (LEN 4): Each consumable
          Field 22 (varint): usage counter value
    """
    result: list[ConsumableUsage] = []
    try:
        raw = base64.b64decode(b64_value)
        data = strip_length_prefix(raw)
        outer = decode_message(data)

        # Field 1 is the container
        container_bytes = get_bytes(outer, 1)
        if not container_bytes:
            return result

        container = decode_message(container_bytes)

        for field_id, name in CONSUMABLE_FIELD_MAP.items():
            consumable_bytes = get_bytes(container, field_id)
            if consumable_bytes is None or len(consumable_bytes) == 0:
                continue

            # Each consumable contains field 22 with the usage varint
            inner = decode_message(consumable_bytes)
            raw_value = get_varint(inner, 22)

            max_val = CONSUMABLE_MAX_VALUES.get(name)
            life_pct = None
            if max_val and max_val > 0:
                life_pct = max(0.0, round(100.0 - (raw_value / max_val * 100.0), 1))

            result.append(ConsumableUsage(
                name=name,
                field_id=field_id,
                raw_value=raw_value,
                max_value=max_val,
                life_remaining_pct=life_pct,
            ))

    except Exception as e:
        _LOGGER.debug("Error parsing DPS 168: %s", e)

    return result


# ─── DPS 164: Room Definitions ────────────────────────────────────────────────


# Eufy room type codes → default English names.
# Sourced from Eufy app room-type presets.  Unknown codes fall back to
# "Room <id>".
EUFY_ROOM_TYPES: dict[int, str] = {
    1: "Living Room",
    2: "Master Bedroom",
    3: "Guest Bedroom",
    4: "Kids Room",
    5: "Study",
    6: "Kitchen",
    7: "Dining Room",
    8: "Bathroom",
    9: "Balcony",
    10: "Hallway",
    11: "Laundry Room",
    12: "Storage Room",
    13: "Garage",
    14: "Gym",
    15: "Office",
    16: "Corridor",
    17: "Entrance",
    18: "Cloakroom",
    19: "Playroom",
    20: "Pet Room",
    21: "Closet",
    22: "Pantry",
    23: "Nursery",
    24: "Den",
    25: "Sunroom",
    26: "Attic",
    27: "Basement",
    28: "Workshop",
    29: "Patio",
    30: "Foyer",
    31: "Mud Room",
    32: "Utility Room",
    33: "Sitting Room",
    34: "Lounge",
    35: "Library",
    36: "Theater Room",
    37: "Music Room",
    38: "Bar",
    39: "Wine Cellar",
    40: "Guest Bath",
}


@dataclass
class RoomDefinition:
    """A room definition from DPS 164."""
    room_id: int = 0
    room_type: int = 0
    default_name: str = ""
    raw_fields: dict = field(default_factory=dict)


def room_type_to_name(room_type: int, room_id: int) -> str:
    """Resolve a room type code to a human-readable default name."""
    return EUFY_ROOM_TYPES.get(room_type, f"Room {room_id}")


def parse_dps164(b64_value: str) -> list[RoomDefinition]:
    """Parse DPS 164 room definitions from base64-encoded protobuf.

    Structure (varint length-prefixed):
      Field 1 (varint): config version / room count hint
      Field 2 (varint): unknown
      Field 3 (LEN): empty
      Field 4 (LEN, repeated): Room definition
        - Field 1 (LEN): sub-message → field 1 (varint): room_id
        - Field 3 (LEN): cleaning config sub-message
            - Field 3 (LEN): sub-message → field 1 (varint): room_type code
    """
    rooms: list[RoomDefinition] = []
    try:
        raw = base64.b64decode(b64_value)
        data = strip_length_prefix(raw)
        outer = decode_message(data)

        # Field 4 is repeated — each occurrence is a room
        room_entries = outer.get(4, [])
        for room_bytes in room_entries:
            if not isinstance(room_bytes, bytes):
                continue

            room_fields = decode_message(room_bytes)

            # Field 1 contains a sub-message with field 1 = room_id
            room_id = 0
            id_bytes = get_bytes(room_fields, 1)
            if id_bytes:
                id_fields = decode_message(id_bytes)
                room_id = get_varint(id_fields, 1)

            # Room type is at field 3 → field 3 → field 1
            room_type = 0
            f3_bytes = get_bytes(room_fields, 3)
            if f3_bytes:
                f3_fields = decode_message(f3_bytes)
                f3_3_bytes = get_bytes(f3_fields, 3)
                if f3_3_bytes:
                    f3_3_fields = decode_message(f3_3_bytes)
                    room_type = get_varint(f3_3_fields, 1)

            rooms.append(RoomDefinition(
                room_id=room_id,
                room_type=room_type,
                default_name=room_type_to_name(room_type, room_id),
                raw_fields={k: v for k, v in room_fields.items()},
            ))

        _LOGGER.debug("Parsed %d rooms from DPS 164: %s",
                       len(rooms),
                       [(r.room_id, r.room_type, r.default_name) for r in rooms])

    except Exception as e:
        _LOGGER.debug("Error parsing DPS 164: %s", e)

    return rooms
