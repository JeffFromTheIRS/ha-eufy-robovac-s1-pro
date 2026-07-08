"""Decode DPS 165 (room list + map id) and build the room-clean command.

Uses the integration's existing hand-rolled protobuf helpers so we don't need
the full generated .proto set — DPS 165 is a small ``UniversalDataResponse`` and
the room-clean command a small ``ModeCtrlRequest``:

    UniversalDataResponse { RoomTable cur_map_room = 1 }
    RoomTable  { uint32 map_id = 1; repeated Data data = 2 }
    Data       { uint32 id = 1; string name = 2 }

    ModeCtrlRequest { method = 1 (START_SELECT_ROOMS_CLEAN);
                      SelectRoomsClean select_rooms_clean = 4 }
    SelectRoomsClean { repeated Room{id=1,order=2} rooms = 1;
                       clean_times = 2; map_id = 3; mode = 5 }
"""

from __future__ import annotations

import base64
import logging

from ..protobuf_parser import decode_message, get_bytes, get_varint, strip_length_prefix

_LOGGER = logging.getLogger(__name__)


def parse_rooms(dps165_value: str) -> tuple[int | None, list[dict]]:
    """Return ``(map_id, [{"id": int, "name": str}, ...])`` from DPS 165."""
    try:
        raw = base64.b64decode(dps165_value)
        top = decode_message(strip_length_prefix(raw))
        room_table = get_bytes(top, 1)  # cur_map_room
        if not room_table:
            return None, []
        rt = decode_message(room_table)
        map_id = get_varint(rt, 1)
        rooms: list[dict] = []
        for entry in rt.get(2, []):  # repeated Data
            if not isinstance(entry, (bytes, bytearray)):
                continue
            d = decode_message(entry)
            rid = get_varint(d, 1)
            name_bytes = get_bytes(d, 2)
            name = name_bytes.decode("utf-8", "replace") if name_bytes else f"Room {rid}"
            rooms.append({"id": rid, "name": name})
        return map_id, rooms
    except Exception as e:  # noqa: BLE001 - never let a decode error break the coordinator
        _LOGGER.debug("Failed to parse DPS 165: %s", e)
        return None, []


def _varint(n: int) -> bytes:
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | 0x80]) if n else bytes([b])
        if not n:
            return out


def _vf(field: int, value: int) -> bytes:
    """Varint field, omitting proto3 zero-defaults."""
    return b"" if value == 0 else _varint(field << 3) + _varint(value)


def _ld(field: int, data: bytes) -> bytes:
    """Length-delimited field."""
    return _varint((field << 3) | 2) + _varint(len(data)) + data


def build_room_clean_command(
    room_ids: list[int], map_id: int, mode: int = 0, clean_times: int = 1
) -> str:
    """Base64 ``ModeCtrlRequest`` for DPS 152 that starts a select-rooms clean.

    ``mode`` 0 = GENERAL, 1 = CUSTOMIZE. Verified to match eufy-clean's
    ``build_room_clean_command`` byte-for-byte.
    """
    rooms = b"".join(
        _ld(1, _vf(1, rid) + _vf(2, i + 1)) for i, rid in enumerate(room_ids)
    )
    select = rooms + _vf(2, clean_times) + _vf(3, map_id) + _vf(5, mode)
    mode_ctrl = _vf(1, 1) + _ld(4, select)  # method=1 START_SELECT_ROOMS_CLEAN
    return base64.b64encode(_varint(len(mode_ctrl)) + mode_ctrl).decode()
