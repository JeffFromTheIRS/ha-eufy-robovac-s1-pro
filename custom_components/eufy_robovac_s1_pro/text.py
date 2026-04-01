"""Text platform for Eufy RoboVac S1 Pro — editable room names.

Creates one text entity per room discovered in DPS 164.  Each entity
defaults to the name resolved from the Eufy room-type code (e.g.
type 8 = "Bathroom") and can be edited by the user.  Custom names are
persisted in the config entry's options dict so they survive restarts.

If DPS 164 is not yet available at setup time (the S1 Pro does not
broadcast it every poll cycle), no text entities are created until the
next integration reload or HA restart once the data has been cached.
"""

import logging
from typing import Any

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_COORDINATOR, CONF_DISCOVERED_DEVICES, DOMAIN
from .mixins import CoordinatorTuyaDeviceUniqueIDMixin
from .protobuf_parser import parse_dps164, room_type_to_name

logger = logging.getLogger(__name__)

# Config-entry options key where custom room names are stored.
# Format: {"room_names": {"1": "My Living Room", "3": "Office", ...}}
CONF_ROOM_NAMES = "room_names"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up editable room-name text entities."""
    discovered_devices = hass.data[DOMAIN][config_entry.entry_id][CONF_DISCOVERED_DEVICES]

    entities: list[TextEntity] = []

    for device_id, props in discovered_devices.items():
        coordinator = props[CONF_COORDINATOR]

        # Try to get rooms from current coordinator data
        dps164 = (coordinator.data or {}).get("164", "")
        if not dps164:
            logger.debug(
                "DPS 164 not available yet — room name entities will appear "
                "after HA restart once room data has been cached"
            )
            continue

        rooms = parse_dps164(dps164)
        if not rooms:
            continue

        # Load any previously-saved custom names from config entry options
        saved_names: dict[str, str] = config_entry.options.get(CONF_ROOM_NAMES, {})

        for room in rooms:
            entities.append(
                RoomNameText(
                    coordinator=coordinator,
                    config_entry=config_entry,
                    room_id=room.room_id,
                    room_type=room.room_type,
                    saved_name=saved_names.get(str(room.room_id)),
                )
            )

    if entities:
        async_add_entities(entities)


class RoomNameText(CoordinatorTuyaDeviceUniqueIDMixin, CoordinatorEntity, TextEntity):
    """Editable text entity for a single room's display name.

    The value is persisted in the config entry's options dict under
    ``room_names.<room_id>``.  When the user clears the value, it
    reverts to the default name derived from the Eufy room-type code.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:rename"
    _attr_mode = "text"
    _attr_native_max = 64
    _attr_native_min = 1
    _attr_pattern = None  # accept anything

    def __init__(
        self,
        coordinator,
        config_entry: ConfigEntry,
        room_id: int,
        room_type: int,
        saved_name: str | None,
    ):
        self._room_id = room_id
        self._room_type = room_type
        self._config_entry = config_entry
        self._default_name = room_type_to_name(room_type, room_id)
        self._custom_name = saved_name
        self._attr_name = f"Room {room_id} Name"
        super().__init__(coordinator=coordinator)

    @property
    def native_value(self) -> str:
        """Return the custom name, or the default if none is set."""
        return self._custom_name or self._default_name

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "room_id": self._room_id,
            "room_type": self._room_type,
            "default_name": self._default_name,
            "is_custom": self._custom_name is not None,
        }

    async def async_set_value(self, value: str) -> None:
        """Update the room name and persist it in config entry options."""
        value = value.strip()
        if not value or value == self._default_name:
            # Revert to default
            self._custom_name = None
        else:
            self._custom_name = value

        # Persist to config entry options
        current_options = dict(self._config_entry.options)
        room_names: dict[str, str] = dict(current_options.get(CONF_ROOM_NAMES, {}))

        if self._custom_name:
            room_names[str(self._room_id)] = self._custom_name
        else:
            room_names.pop(str(self._room_id), None)

        current_options[CONF_ROOM_NAMES] = room_names
        self.hass.config_entries.async_update_entry(
            self._config_entry, options=current_options
        )

        self.async_write_ha_state()
        logger.info(
            "Room %d name %s to: %s",
            self._room_id,
            "reset" if not self._custom_name else "set",
            self.native_value,
        )
