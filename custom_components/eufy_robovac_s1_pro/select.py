"""Select platform for Eufy Robovac."""
import base64
import logging
import asyncio

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_CLOUD_SESSION, CONF_COORDINATOR, CONF_DISCOVERED_DEVICES, DOMAIN
from .mixins import CoordinatorTuyaDeviceUniqueIDMixin
from .protobuf_parser import decode_message, get_bytes, strip_length_prefix

logger = logging.getLogger(__name__)

# S1 Pro Cleaning Mode definitions — simplified to two options.
# Water level is controlled separately via the Mop Intensity select entity.
CLEANING_MODES = {
    "vacuum": {
        "name": "Vacuum",
        "dps154": "FAoKCgASABoAIgIIAhIGCAEQASAB",
        "dps10": None,
    },
    "vacuum_and_mop": {
        "name": "Vacuum and Mop",
        # Default to medium water level; Mop Intensity select handles changes
        "dps154": "FgoMCgIIAhIAGgAiAggBEgYIARABIAE=",
        "dps10": "middle",
    },
}

def _is_vacuum_only_mode(dps154: str) -> bool:
    """Check if DPS 154 protobuf represents vacuum-only mode (no mop).

    DPS 154 encodes both cleaning mode and suction level as a protobuf.
    Field 1 is a sub-message whose field 1 holds mop config bytes.
    If the mop config is empty or absent, the vacuum is in vacuum-only mode.
    """
    try:
        raw = base64.b64decode(dps154)
        data = strip_length_prefix(raw)
        fields = decode_message(data)
        f1_bytes = get_bytes(fields, 1)
        if f1_bytes is None:
            return True
        f1_fields = decode_message(f1_bytes)
        mop_config = get_bytes(f1_fields, 1)
        return mop_config is None or len(mop_config) == 0
    except Exception:
        return True


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the select platform."""
    discovered_devices = hass.data[DOMAIN][config_entry.entry_id][CONF_DISCOVERED_DEVICES]

    logger.debug("Setting up select entities for discovered devices: %s", discovered_devices)

    entities = []
    for device_id, props in discovered_devices.items():
        coordinator = props[CONF_COORDINATOR]
        entities.append(CleaningModeSelect(coordinator=coordinator))
        entities.append(SuctionLevelSelect(coordinator=coordinator))
        entities.append(MopIntensitySelect(coordinator=coordinator))

    # Room cleaning is only available via the optional cloud session.
    cloud_session = hass.data[DOMAIN][config_entry.entry_id].get(CONF_CLOUD_SESSION)
    if cloud_session is not None and discovered_devices:
        first = next(iter(discovered_devices.values()))
        entities.append(
            RoomCleanSelect(coordinator=first[CONF_COORDINATOR], session=cloud_session)
        )

    async_add_entities(entities)


class CleaningModeSelect(CoordinatorTuyaDeviceUniqueIDMixin, CoordinatorEntity, RestoreEntity, SelectEntity):
    """Select entity for cleaning mode."""

    _attr_name = "Cleaning Mode"
    _attr_icon = "mdi:broom"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator):
        """Initialize the select entity."""
        self._restored_option = None
        # Override unique_id to use underscore format for backwards compatibility
        # with entities created before the mixin switch (which uses dashes).
        self._attr_unique_id = f"{coordinator.tuya_client.device_id}_cleaning_mode"
        super().__init__(coordinator=coordinator)

    async def async_added_to_hass(self) -> None:
        """Restore last known value on startup."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in (None, "unknown", "unavailable"):
            valid_options = [CLEANING_MODES[m]["name"] for m in CLEANING_MODES]
            if last_state.state in valid_options:
                self._restored_option = last_state.state
                logger.debug("Restored Cleaning Mode: %s", self._restored_option)

    @property
    def options(self) -> list[str]:
        """Return available options."""
        return [CLEANING_MODES[mode]["name"] for mode in CLEANING_MODES.keys()]

    @property
    def current_option(self) -> str | None:
        """Return the currently selected option based on DPS 154 protobuf parsing."""
        if not self.coordinator.data:
            return self._restored_option

        dps154 = self.coordinator.data.get("154", "")
        if dps154 and not _is_vacuum_only_mode(dps154):
            return CLEANING_MODES["vacuum_and_mop"]["name"]

        return CLEANING_MODES["vacuum"]["name"]

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        selected_mode = None
        for mode_key, mode_config in CLEANING_MODES.items():
            if mode_config["name"] == option:
                selected_mode = mode_key
                break

        if not selected_mode:
            logger.error("Invalid cleaning mode selected: %s", option)
            return

        mode_config = CLEANING_MODES[selected_mode]
        logger.info("Setting cleaning mode to: %s", mode_config["name"])

        try:
            if selected_mode == "vacuum_and_mop":
                # When switching to mop mode, respect the current Mop Intensity
                # setting if one exists, otherwise use the default (medium)
                current_water = self.coordinator.data.get("10") if self.coordinator.data else None
                if isinstance(current_water, str) and current_water in ("low", "middle", "high"):
                    water_to_dps154 = {
                        "low": "FAoKCgIIAhIAGgAiABIGCAEQASAB",
                        "middle": "FgoMCgIIAhIAGgAiAggBEgYIARABIAE=",
                        "high": "FgoMCgIIAhIAGgAiAggCEgYIARABIAE=",
                    }
                    dps154 = water_to_dps154[current_water]
                else:
                    dps154 = mode_config["dps154"]
                    current_water = mode_config["dps10"]

                await self.coordinator.tuya_client.async_set({"154": dps154})
                await asyncio.sleep(0.3)
                if current_water:
                    await self.coordinator.tuya_client.async_set({"10": current_water})
            else:
                # Vacuum only
                await self.coordinator.tuya_client.async_set({"154": mode_config["dps154"]})

            await asyncio.sleep(0.3)

            # Re-send current suction level so the hardcoded DPS 154 values
            # (which embed suction=Standard) don't override the user's setting.
            if self.coordinator.data:
                for dps_key in ("9", "158"):
                    raw = self.coordinator.data.get(dps_key)
                    if raw and raw in _SUCTION_REVERSE:
                        dps9_val, dps158_val = SUCTION_LEVELS[_SUCTION_REVERSE[raw]]
                        await self.coordinator.tuya_client.async_set({
                            "9": dps9_val,
                            "158": dps158_val,
                        })
                        break

            await asyncio.sleep(0.5)
            await self.coordinator.async_request_refresh()
            logger.info("Cleaning mode set to: %s", mode_config["name"])
        except Exception as e:
            logger.error("Failed to set cleaning mode: %s", e)


# ─── Suction Level (fan speed as standalone select for Matter Hub) ────────────

# Maps display name → (DPS 9 value, DPS 158 value)
SUCTION_LEVELS = {
    "Quiet": ("gentle", "Quiet"),
    "Standard": ("normal", "Standard"),
    "Turbo": ("strong", "Turbo"),
    "Maximum": ("max", "Max"),
}

# Reverse lookup from DPS 9 or DPS 158 values → display name
_SUCTION_REVERSE = {}
for _name, (_dps9, _dps158) in SUCTION_LEVELS.items():
    _SUCTION_REVERSE[_dps9] = _name
    _SUCTION_REVERSE[_dps158] = _name


class SuctionLevelSelect(CoordinatorTuyaDeviceUniqueIDMixin, CoordinatorEntity, RestoreEntity, SelectEntity):
    """Standalone select entity for vacuum suction / fan speed level.

    Reads from DPS 9 (primary) or DPS 158 (fallback). Writes to both.
    Exposed as a separate entity so Matter Hub can discover it as
    'Suction Level' independently of the vacuum entity's fan_speed.
    """

    _attr_name = "Suction Level"
    _attr_icon = "mdi:fan"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator):
        self._restored_option = None
        super().__init__(coordinator=coordinator)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state in SUCTION_LEVELS:
            self._restored_option = last_state.state

    @property
    def options(self) -> list[str]:
        return list(SUCTION_LEVELS.keys())

    @property
    def current_option(self) -> str | None:
        if self.coordinator.data:
            for dps_key in ("9", "158"):
                raw = self.coordinator.data.get(dps_key)
                if raw and raw in _SUCTION_REVERSE:
                    return _SUCTION_REVERSE[raw]
        return self._restored_option or "Standard"

    async def async_select_option(self, option: str) -> None:
        if option not in SUCTION_LEVELS:
            logger.error("Invalid suction level: %s", option)
            return

        dps9_val, dps158_val = SUCTION_LEVELS[option]
        logger.info("Setting suction level to: %s", option)

        try:
            await self.coordinator.tuya_client.async_set({
                "9": dps9_val,
                "158": dps158_val,
            })
            await asyncio.sleep(0.5)
            await self.coordinator.async_request_refresh()
        except Exception as e:
            logger.error("Failed to set suction level: %s", e)


# ─── Mop Intensity / Water Level (standalone select for Matter Hub) ───────────

# Maps display name → DPS 10 value
WATER_LEVELS = {
    "Off": None,      # Vacuum-only mode (no mop)
    "Low": "low",
    "Medium": "middle",
    "High": "high",
}

_WATER_REVERSE = {v: k for k, v in WATER_LEVELS.items() if v is not None}


class MopIntensitySelect(CoordinatorTuyaDeviceUniqueIDMixin, CoordinatorEntity, RestoreEntity, SelectEntity):
    """Standalone select entity for mop water level / intensity.

    Reads from DPS 10. When changed, also updates DPS 154 (cleaning mode
    protobuf) to match, because the S1 Pro uses both DPS to define the
    cleaning configuration.
    """

    _attr_name = "Mop Intensity"
    _attr_icon = "mdi:water"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator):
        self._restored_option = None
        super().__init__(coordinator=coordinator)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state in WATER_LEVELS:
            self._restored_option = last_state.state

    @property
    def options(self) -> list[str]:
        return list(WATER_LEVELS.keys())

    @property
    def current_option(self) -> str | None:
        if self.coordinator.data:
            dps10 = self.coordinator.data.get("10")
            if isinstance(dps10, str) and dps10 in _WATER_REVERSE:
                return _WATER_REVERSE[dps10]

            # If DPS 10 is absent/unset, check if we're in vacuum-only mode
            dps154 = self.coordinator.data.get("154", "")
            if dps154 and _is_vacuum_only_mode(dps154):
                return "Off"

        return self._restored_option or "Off"

    async def async_select_option(self, option: str) -> None:
        if option not in WATER_LEVELS:
            logger.error("Invalid mop intensity: %s", option)
            return

        logger.info("Setting mop intensity to: %s", option)

        # DPS 154 protobuf values for each water level
        _WATER_DPS154 = {
            "low": "FAoKCgIIAhIAGgAiABIGCAEQASAB",
            "middle": "FgoMCgIIAhIAGgAiAggBEgYIARABIAE=",
            "high": "FgoMCgIIAhIAGgAiAggCEgYIARABIAE=",
        }

        try:
            if option == "Off":
                # Switch to vacuum-only mode
                await self.coordinator.tuya_client.async_set({
                    "154": CLEANING_MODES["vacuum"]["dps154"],
                })
            else:
                water_val = WATER_LEVELS[option]
                dps154 = _WATER_DPS154.get(water_val)
                if dps154:
                    await self.coordinator.tuya_client.async_set({"154": dps154})
                    await asyncio.sleep(0.3)
                    await self.coordinator.tuya_client.async_set({"10": water_val})

            await asyncio.sleep(0.5)
            await self.coordinator.async_request_refresh()
        except Exception as e:
            logger.error("Failed to set mop intensity: %s", e)


# ─── Room cleaning (cloud only) ───────────────────────────────────────────────


class RoomCleanSelect(CoordinatorTuyaDeviceUniqueIDMixin, SelectEntity):
    """Select a room to clean, via the optional Eufy cloud session.

    Rooms/map data are not available over the local channel, so this entity is
    only created when the cloud session is enabled and connected. Picking a room
    starts cleaning it; the list of rooms is populated from DPS 165 pushed over
    MQTT and updated live.
    """

    _attr_name = "Clean Room"
    _attr_icon = "mdi:floor-plan"

    def __init__(self, coordinator, session):
        # ``coordinator`` is the local coordinator, used only so the mixin binds
        # this entity to the same device (device_info + unique_id).
        self.coordinator = coordinator
        self._session = session
        self._last_selected: str | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._session.set_update_callback(self._on_cloud_update)

    @callback
    def _on_cloud_update(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return bool(self._session.rooms)

    @property
    def options(self) -> list[str]:
        return [r["name"] for r in self._session.rooms]

    @property
    def current_option(self) -> str | None:
        # Action-style select: reflect the last room we were asked to clean.
        return self._last_selected

    async def async_select_option(self, option: str) -> None:
        for room in self._session.rooms:
            if room["name"] == option:
                self._last_selected = option
                self.async_write_ha_state()
                await self._session.send_room_clean([room["id"]])
                return
        logger.error("Unknown room selected: %s", option)
