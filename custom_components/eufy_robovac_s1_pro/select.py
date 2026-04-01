"""Select platform for Eufy Robovac."""
import logging
from typing import Any
import asyncio

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_COORDINATOR, CONF_DISCOVERED_DEVICES, DOMAIN
from .mixins import CoordinatorTuyaDeviceUniqueIDMixin

logger = logging.getLogger(__name__)

# S1 Pro Cleaning Mode definitions
CLEANING_MODES = {
    "vacuum": {
        "name": "Vacuum Only",
        "dps154": "FAoKCgASABoAIgIIAhIGCAEQASAB",
        "dps10": None
    },
    "mop_low": {
        "name": "Vacuum and Mop (Water Level: Low)",
        "dps154": "FAoKCgIIAhIAGgAiABIGCAEQASAB",
        "dps10": "low"
    },
    "mop_middle": {
        "name": "Vacuum and Mop (Water Level: Medium)",
        "dps154": "FgoMCgIIAhIAGgAiAggBEgYIARABIAE=",
        "dps10": "middle"
    },
    "mop_high": {
        "name": "Vacuum and Mop (Water Level: High)",
        "dps154": "FgoMCgIIAhIAGgAiAggCEgYIARABIAE=",
        "dps10": "high"
    }
}

# Map DPS values to mode names
DPS_TO_MODE_MAP = {
    ("FAoKCgASABoAIgIIAhIGCAEQASAB", None): "vacuum",
    ("FAoKCgIIAhIAGgAiABIGCAEQASAB", "low"): "mop_low",
    ("FgoMCgIIAhIAGgAiAggBEgYIARABIAE=", "middle"): "mop_middle",
    ("FgoMCgIIAhIAGgAiAggCEgYIARABIAE=", "high"): "mop_high",
}


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

    async_add_entities(entities)


class CleaningModeSelect(CoordinatorEntity, RestoreEntity, SelectEntity):
    """Select entity for cleaning mode.
    
    RestoreEntity を使用して再起動後もDPSが読めるまで最終値を保持します。
    """

    _attr_name = "Cleaning Mode"
    _attr_icon = "mdi:broom"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator):
        """Initialize the select entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.tuya_client.device_id}_cleaning_mode"
        self._restored_option = None
    
    async def async_added_to_hass(self) -> None:
        """Restore last known value on startup."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in (None, "unknown", "unavailable"):
            # Validate that restored value is a known option
            valid_options = [CLEANING_MODES[m]["name"] for m in CLEANING_MODES]
            if last_state.state in valid_options:
                self._restored_option = last_state.state
                logger.debug("Restored Cleaning Mode: %s", self._restored_option)
        
    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.tuya_client.device_id)},
            manufacturer="Eufy",
            name="Eufy Robovac S1 Pro",
            model="S1 Pro (T2080)",
        )

    @property
    def options(self) -> list[str]:
        """Return available options."""
        return [CLEANING_MODES[mode]["name"] for mode in CLEANING_MODES.keys()]

    @property
    def current_option(self) -> str | None:
        """Return the currently selected option."""
        if not self.coordinator.data:
            return self._restored_option
        
        dps154 = self.coordinator.data.get("154", "")
        dps10 = self.coordinator.data.get("10", None)
        
        # Check if DPS 10 is a string (water level)
        if isinstance(dps10, str) and dps10 in ["low", "middle", "high"]:
            water_level = dps10
        else:
            water_level = None
        
        # Try to find matching mode
        mode_key = (dps154, water_level)
        if mode_key in DPS_TO_MODE_MAP:
            mode = DPS_TO_MODE_MAP[mode_key]
            if mode in CLEANING_MODES:
                return CLEANING_MODES[mode]["name"]
        
        # Try without water level (vacuum mode)
        if dps154 == CLEANING_MODES["vacuum"]["dps154"]:
            return CLEANING_MODES["vacuum"]["name"]

        # DPS 154 value doesn't match any known pattern — use restored or default
        return self._restored_option or CLEANING_MODES["vacuum"]["name"]

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        # Find the mode by name
        selected_mode = None
        for mode_key, mode_config in CLEANING_MODES.items():
            if mode_config["name"] == option:
                selected_mode = mode_key
                break
        
        if not selected_mode:
            logger.error(f"Invalid cleaning mode selected: {option}")
            return
        
        mode_config = CLEANING_MODES[selected_mode]
        logger.info(f"Setting cleaning mode to: {mode_config['name']}")
        
        try:
            # Set DPS 154
            await self.coordinator.tuya_client.async_set({"154": mode_config["dps154"]})
            await asyncio.sleep(0.5)
            
            # Set DPS 10 if needed (for mopping modes)
            if mode_config["dps10"]:
                await self.coordinator.tuya_client.async_set({"10": mode_config["dps10"]})
            await asyncio.sleep(0.5)
            
            # Refresh state
            await self.coordinator.async_request_refresh()
            
            logger.info(f"Cleaning mode set to: {mode_config['name']}")
        except Exception as e:
            logger.error(f"Failed to set cleaning mode: {e}")


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
            if dps154 == CLEANING_MODES.get("vacuum", {}).get("dps154"):
                return "Off"

        return self._restored_option or "Off"

    async def async_select_option(self, option: str) -> None:
        if option not in WATER_LEVELS:
            logger.error("Invalid mop intensity: %s", option)
            return

        logger.info("Setting mop intensity to: %s", option)

        try:
            if option == "Off":
                # Switch to vacuum-only mode
                vacuum_cfg = CLEANING_MODES["vacuum"]
                await self.coordinator.tuya_client.async_set({
                    "154": vacuum_cfg["dps154"],
                })
            else:
                # Find the matching mop cleaning mode
                water_val = WATER_LEVELS[option]
                # Map water level to the corresponding cleaning mode key
                water_to_mode = {"low": "mop_low", "middle": "mop_middle", "high": "mop_high"}
                mode_key = water_to_mode.get(water_val)
                if mode_key and mode_key in CLEANING_MODES:
                    mode_cfg = CLEANING_MODES[mode_key]
                    await self.coordinator.tuya_client.async_set({
                        "154": mode_cfg["dps154"],
                    })
                    await asyncio.sleep(0.3)
                    await self.coordinator.tuya_client.async_set({
                        "10": water_val,
                    })

            await asyncio.sleep(0.5)
            await self.coordinator.async_request_refresh()
        except Exception as e:
            logger.error("Failed to set mop intensity: %s", e)
