"""Sensor platform for Eufy RoboVac S1 Pro.

Sensors are organized by data source:
- Battery: DPS 8/163 (direct numeric values)
- Running Status: DPS 153 (binary pattern-decoded in vacuum.py)
- Cleaning Statistics: DPS 167 (protobuf — last clean, totals)
- Consumable Life: DPS 168 (protobuf — usage counters)
- Room Definitions: DPS 164 (protobuf — diagnostic only)
- Raw DPS: DPS 116/117/121/140 (room/map exploration — format unknown)
"""

import base64

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.icon import icon_for_battery_level
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
import logging

from .const import CONF_COORDINATOR, CONF_DISCOVERED_DEVICES, DOMAIN
from .coordinators import EufyTuyaDataUpdateCoordinator
from .mixins import CoordinatorTuyaDeviceUniqueIDMixin
from .protobuf_parser import parse_dps167, parse_dps168, parse_dps164
from .vacuum import decode_dps153_to_state, SUBSTATUS_DESCRIPTIONS, RobovacState

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_devices: AddEntitiesCallback,
) -> None:
    discovered_devices = hass.data[DOMAIN][config_entry.entry_id][CONF_DISCOVERED_DEVICES]

    devices = []

    for device_id, props in discovered_devices.items():
        coordinator = props[CONF_COORDINATOR]

        # Always-present sensors
        devices.append(BatteryPercentageSensor(coordinator=coordinator))
        devices.append(RunningStatusSensor(coordinator=coordinator))

        # DPS 167: Cleaning statistics (protobuf)
        # Register unconditionally — RestoreEntity handles missing data
        devices.append(TotalCleaningCountSensor(coordinator=coordinator))
        devices.append(TotalCleaningAreaSensor(coordinator=coordinator))
        devices.append(TotalCleaningTimeSensor(coordinator=coordinator))
        devices.append(LastCleanTimeSensor(coordinator=coordinator))
        devices.append(LastCleanAreaSensor(coordinator=coordinator))

        # DPS 168: Consumable life sensors (protobuf)
        consumables = [
            (1, "side_brush", "Side Brush Life", "mdi:broom"),
            (2, "main_brush", "Main Brush Life", "mdi:broom"),
            (3, "filter", "Filter Life", "mdi:air-filter"),
            (5, "sensor", "Sensor Life", "mdi:leak"),
            (6, "mop_pad", "Mop Pad Life", "mdi:spray-bottle"),
        ]
        for field_id, consumable_key, display_name, icon in consumables:
            devices.append(ConsumableLifeProtobufSensor(
                coordinator=coordinator,
                consumable_field_id=field_id,
                consumable_key=consumable_key,
                name=display_name,
                icon=icon,
            ))

        # DPS 164: Room definitions (diagnostic — decode and display)
        devices.append(RoomDefinitionsSensor(coordinator=coordinator))

        # Raw DPS diagnostic sensors for room/map data exploration
        # These DPS keys may only appear when room cleaning is triggered
        # from the Eufy app. Capturing them helps decode the data format.
        raw_dps_sensors = [
            ("140", "Smart Rooms Data", "mdi:floor-plan"),
            ("116", "Area Clean Data", "mdi:selection-ellipse"),
            ("117", "Area Clean Active", "mdi:play-circle-outline"),
            ("121", "Map Data", "mdi:map"),
        ]
        for dps_id, name, icon in raw_dps_sensors:
            devices.append(RawDPSDiagnosticSensor(
                coordinator=coordinator,
                dps_id=dps_id,
                name=name,
                icon=icon,
            ))

    if devices:
        return async_add_devices(devices)


# ─── Battery ──────────────────────────────────────────────────────────────────


class BatteryPercentageSensor(CoordinatorTuyaDeviceUniqueIDMixin, CoordinatorEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_name = "Battery"

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None and ("8" in self.coordinator.data or "163" in self.coordinator.data)

    @property
    def icon(self) -> str:
        mode = (self.coordinator.data or {}).get("5", "")
        charging = mode in ["charge", "docked", "Charging"]
        return icon_for_battery_level(self.native_value, charging=charging)

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data:
            for dps_key in ("8", "163"):
                value = self.coordinator.data.get(dps_key)
                if value is not None:
                    try:
                        battery = int(value)
                        if 0 <= battery <= 100:
                            return battery
                    except (ValueError, TypeError):
                        pass
        return None


# ─── Running Status ───────────────────────────────────────────────────────────


class RunningStatusSensor(CoordinatorTuyaDeviceUniqueIDMixin, CoordinatorEntity, RestoreEntity, SensorEntity):
    """Detailed running status decoded from DPS 153."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Running Status"
    _attr_icon = "mdi:robot-vacuum"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._restored_value = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in (None, "unknown", "unavailable"):
            self._restored_value = last_state.state

    @property
    def available(self) -> bool:
        has_live = self.coordinator.data is not None and ("153" in self.coordinator.data or "2" in self.coordinator.data)
        return has_live or self._restored_value is not None

    @property
    def native_value(self) -> str:
        if not self.coordinator.data:
            return self._restored_value or "Unknown"

        dps153 = self.coordinator.data.get("153", "")
        if dps153:
            detected_state, substatus = decode_dps153_to_state(dps153)
            return SUBSTATUS_DESCRIPTIONS.get(substatus, "Unknown")

        dps2 = self.coordinator.data.get("2")
        if dps2 is True:
            return "Running"
        elif dps2 is False:
            return "Stopped"

        return "Unknown"

    @property
    def icon(self) -> str:
        if not self.coordinator.data:
            return "mdi:robot-vacuum"

        dps153 = self.coordinator.data.get("153", "")
        if dps153:
            detected_state, substatus = decode_dps153_to_state(dps153)
            icon_map = {
                RobovacState.CLEANING: "mdi:robot-vacuum",
                RobovacState.PAUSED: "mdi:pause-circle",
                RobovacState.RETURNING: "mdi:home-import-outline",
                RobovacState.ERROR: "mdi:alert-circle",
            }
            if detected_state in icon_map:
                return icon_map[detected_state]
            if detected_state == RobovacState.DOCKED:
                docked_icons = {
                    "charging": "mdi:battery-charging",
                    "fully_charged": "mdi:battery-charging",
                    "dust_collecting": "mdi:delete-empty",
                    "mop_washing": "mdi:spray-bottle",
                    "mop_washing_pre": "mdi:spray-bottle",
                    "mop_drying": "mdi:fan",
                    "water_refilling": "mdi:water",
                }
                return docked_icons.get(substatus, "mdi:home")

        return "mdi:robot-vacuum"


# ─── DPS 167: Cleaning Statistics (protobuf) ─────────────────────────────────


class TotalCleaningCountSensor(CoordinatorTuyaDeviceUniqueIDMixin, CoordinatorEntity, RestoreEntity, SensorEntity):
    """Total number of cleaning sessions from DPS 167 protobuf."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Total Cleaning Count"
    _attr_icon = "mdi:counter"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._last_valid = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in (None, "unknown", "unavailable"):
            try:
                self._last_valid = int(last_state.state)
            except (ValueError, TypeError):
                pass

    @property
    def available(self) -> bool:
        return (self.coordinator.data is not None and "167" in self.coordinator.data) or self._last_valid is not None

    @property
    def native_value(self) -> int | None:
        dps167 = (self.coordinator.data or {}).get("167", "")
        if dps167:
            stats = parse_dps167(dps167)
            if stats and stats.total_count > 0:
                if self._last_valid is None or stats.total_count >= self._last_valid:
                    self._last_valid = stats.total_count
        return self._last_valid


class TotalCleaningAreaSensor(CoordinatorTuyaDeviceUniqueIDMixin, CoordinatorEntity, RestoreEntity, SensorEntity):
    """Total cleaned area from DPS 167 protobuf."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Total Cleaning Area"
    _attr_icon = "mdi:texture-box"
    _attr_native_unit_of_measurement = "m²"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._last_valid = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in (None, "unknown", "unavailable"):
            try:
                self._last_valid = int(last_state.state)
            except (ValueError, TypeError):
                pass

    @property
    def available(self) -> bool:
        return (self.coordinator.data is not None and "167" in self.coordinator.data) or self._last_valid is not None

    @property
    def native_value(self) -> int | None:
        dps167 = (self.coordinator.data or {}).get("167", "")
        if dps167:
            stats = parse_dps167(dps167)
            if stats and stats.total_area_sqm > 0:
                if self._last_valid is None or stats.total_area_sqm >= self._last_valid:
                    self._last_valid = stats.total_area_sqm
        return self._last_valid


class TotalCleaningTimeSensor(CoordinatorTuyaDeviceUniqueIDMixin, CoordinatorEntity, RestoreEntity, SensorEntity):
    """Total cleaning time from DPS 167 protobuf."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Total Cleaning Time"
    _attr_icon = "mdi:clock-outline"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._last_valid = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in (None, "unknown", "unavailable"):
            try:
                self._last_valid = int(float(last_state.state))
            except (ValueError, TypeError):
                pass

    @property
    def available(self) -> bool:
        return (self.coordinator.data is not None and "167" in self.coordinator.data) or self._last_valid is not None

    @property
    def native_value(self) -> int | None:
        dps167 = (self.coordinator.data or {}).get("167", "")
        if dps167:
            stats = parse_dps167(dps167)
            if stats and stats.total_time_seconds > 0:
                if self._last_valid is None or stats.total_time_seconds >= self._last_valid:
                    self._last_valid = stats.total_time_seconds
        return self._last_valid


class LastCleanTimeSensor(CoordinatorTuyaDeviceUniqueIDMixin, CoordinatorEntity, SensorEntity):
    """Last cleaning session duration from DPS 167 protobuf."""

    _attr_name = "Last Clean Time"
    _attr_icon = "mdi:clock-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None and "167" in self.coordinator.data

    @property
    def native_value(self) -> int | None:
        dps167 = (self.coordinator.data or {}).get("167", "")
        if dps167:
            stats = parse_dps167(dps167)
            if stats and stats.last_clean:
                return stats.last_clean.time_seconds
        return None


class LastCleanAreaSensor(CoordinatorTuyaDeviceUniqueIDMixin, CoordinatorEntity, SensorEntity):
    """Last cleaning session area from DPS 167 protobuf."""

    _attr_name = "Last Clean Area"
    _attr_icon = "mdi:texture-box"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "m²"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None and "167" in self.coordinator.data

    @property
    def native_value(self) -> int | None:
        dps167 = (self.coordinator.data or {}).get("167", "")
        if dps167:
            stats = parse_dps167(dps167)
            if stats and stats.last_clean:
                return stats.last_clean.area_sqm
        return None


# ─── DPS 168: Consumable Life (protobuf) ─────────────────────────────────────


class ConsumableLifeProtobufSensor(CoordinatorTuyaDeviceUniqueIDMixin, CoordinatorEntity, SensorEntity):
    """Consumable life remaining %, decoded from DPS 168 protobuf."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, consumable_field_id: int, consumable_key: str, name: str, icon: str):
        self._consumable_field_id = consumable_field_id
        self._consumable_key = consumable_key
        self._attr_name = name
        self._attr_icon = icon
        super().__init__(coordinator=coordinator)

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None and "168" in self.coordinator.data

    @property
    def native_value(self) -> float | None:
        dps168 = (self.coordinator.data or {}).get("168", "")
        if not dps168:
            return None

        consumables = parse_dps168(dps168)
        for c in consumables:
            if c.field_id == self._consumable_field_id:
                return c.life_remaining_pct
        return None

    @property
    def extra_state_attributes(self) -> dict | None:
        """Expose raw usage counter for debugging."""
        dps168 = (self.coordinator.data or {}).get("168", "")
        if not dps168:
            return None

        consumables = parse_dps168(dps168)
        for c in consumables:
            if c.field_id == self._consumable_field_id:
                return {"raw_usage_counter": c.raw_value}
        return None


# ─── DPS 164: Room Definitions (diagnostic) ──────────────────────────────────


class RoomDefinitionsSensor(CoordinatorTuyaDeviceUniqueIDMixin, CoordinatorEntity, SensorEntity):
    """Diagnostic sensor showing decoded room definitions from DPS 164."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Room Definitions"
    _attr_icon = "mdi:floor-plan"

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None and "164" in self.coordinator.data

    @property
    def native_value(self) -> str | None:
        dps164 = (self.coordinator.data or {}).get("164", "")
        if not dps164:
            return None

        rooms = parse_dps164(dps164)
        if not rooms:
            return "No rooms"

        names = [r.default_name for r in rooms]
        return ", ".join(names)

    @property
    def extra_state_attributes(self) -> dict | None:
        """Expose decoded room details."""
        dps164 = (self.coordinator.data or {}).get("164", "")
        if not dps164:
            return None

        rooms = parse_dps164(dps164)
        room_list = []
        for r in rooms:
            room_list.append({
                "room_id": r.room_id,
                "room_type": r.room_type,
                "default_name": r.default_name,
            })

        return {"rooms": room_list, "room_count": len(room_list)}


# ─── Raw DPS Diagnostic Sensors (room/map exploration) ──────────────────────


class RawDPSDiagnosticSensor(CoordinatorTuyaDeviceUniqueIDMixin, CoordinatorEntity, RestoreEntity, SensorEntity):
    """Diagnostic sensor that exposes a raw DPS value as a string.

    Used to capture DPS keys whose data format is unknown (e.g. room/map
    data). The raw value is shown as the sensor state, and if it looks like
    base64 the decoded hex is exposed in extra_state_attributes for analysis.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, dps_id: str, name: str, icon: str):
        self._dps_id = dps_id
        self._attr_name = name
        self._attr_icon = icon
        self._restored_value = None
        super().__init__(coordinator=coordinator)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in (None, "unknown", "unavailable"):
            self._restored_value = last_state.state

    @property
    def available(self) -> bool:
        has_live = self.coordinator.data is not None and self._dps_id in self.coordinator.data
        return has_live or self._restored_value is not None

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data and self._dps_id in self.coordinator.data:
            value = self.coordinator.data[self._dps_id]
            self._restored_value = str(value)
            return str(value)
        return self._restored_value

    @property
    def extra_state_attributes(self) -> dict | None:
        """If the value looks like base64, expose decoded hex for analysis."""
        raw = (self.coordinator.data or {}).get(self._dps_id)
        if raw is None:
            return None

        attrs: dict = {"dps_key": self._dps_id}

        if isinstance(raw, str) and len(raw) >= 4:
            try:
                decoded = base64.b64decode(raw)
                attrs["base64_decoded_hex"] = " ".join(f"{b:02x}" for b in decoded)
                attrs["base64_decoded_length"] = len(decoded)
            except Exception:
                pass

        return attrs
