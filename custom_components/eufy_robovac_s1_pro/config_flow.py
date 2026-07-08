"""Config flow and options flow for Eufy RoboVac S1 Pro."""

import logging
from typing import Any

import voluptuous as vol
from homeassistant import data_entry_flow
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback

from .const import CONF_COORDINATOR, CONF_DISCOVERED_DEVICES, DOMAIN
from .eufy_local_id_grabber.clients import EufyHomeSession
from .protobuf_parser import parse_dps164, room_type_to_name

logger = logging.getLogger(__name__)

EUFY_LOGIN_SCHEMA = vol.Schema({vol.Required("username"): str, vol.Required("password"): str})

CONF_ROOM_NAMES = "room_names"
CONF_CACHED_ROOMS = "cached_rooms"
CONF_ENABLE_CLOUD = "enable_cloud"


class EufyVacuumConfigFlow(ConfigFlow, domain=DOMAIN):
    async def async_step_user(self, user_input: dict[str, str] | None = None) -> data_entry_flow.FlowResult:
        errors = {}

        if user_input is not None:
            username = user_input["username"]
            password = user_input["password"]

            await self.async_set_unique_id(username)
            self._abort_if_unique_id_configured()

            client = EufyHomeSession(username, password)

            try:
                await self.hass.async_add_executor_job(client.get_user_info)
            except Exception:
                logger.exception("Error when logging in with %s", username)

                # TODO: proper exception handling
                errors["username"] = errors["password"] = "Username or password is incorrect"
            else:
                return self.async_create_entry(
                    title=username,
                    data={CONF_EMAIL: username, CONF_PASSWORD: password},
                )

        return self.async_show_form(step_id="user", data_schema=EUFY_LOGIN_SCHEMA)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow handler."""
        return EufyVacuumOptionsFlow(config_entry)


class EufyVacuumOptionsFlow(OptionsFlow):
    """Options flow for editing room names."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    def _get_rooms(self) -> list[tuple[int, int]]:
        """Get (room_id, room_type) pairs from coordinator data or stored cache."""
        rooms: list[tuple[int, int]] = []
        entry_data = self.hass.data.get(DOMAIN, {}).get(self._config_entry.entry_id, {})
        discovered = entry_data.get(CONF_DISCOVERED_DEVICES, {})

        # Try live coordinator data first
        for device_id, props in discovered.items():
            coordinator = props.get(CONF_COORDINATOR)
            if coordinator and coordinator.data:
                dps164 = coordinator.data.get("164", "")
                if dps164:
                    parsed = parse_dps164(dps164)
                    for r in parsed:
                        rooms.append((r.room_id, r.room_type))
                    # Cache rooms in options for future use
                    if rooms:
                        self._cache_rooms(rooms)

        # Fallback to cached rooms if live data unavailable
        if not rooms:
            cached = self._config_entry.options.get(CONF_CACHED_ROOMS, [])
            for entry in cached:
                rooms.append((entry["room_id"], entry["room_type"]))

        return rooms

    def _cache_rooms(self, rooms: list[tuple[int, int]]) -> None:
        """Store room list in config entry options so it survives restarts."""
        cached = [{"room_id": rid, "room_type": rtype} for rid, rtype in rooms]
        current = self._config_entry.options.get(CONF_CACHED_ROOMS, [])
        if cached != current:
            updated_options = dict(self._config_entry.options)
            updated_options[CONF_CACHED_ROOMS] = cached
            self.hass.config_entries.async_update_entry(
                self._config_entry, options=updated_options
            )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        """Show the room name editing form."""
        rooms = self._get_rooms()
        saved_names: dict[str, str] = dict(self._config_entry.options.get(CONF_ROOM_NAMES, {}))
        cloud_enabled = bool(self._config_entry.options.get(CONF_ENABLE_CLOUD, False))

        if user_input is not None:
            # Save the room names
            new_names: dict[str, str] = {}
            for room_id, room_type in rooms:
                key = f"room_{room_id}"
                value = user_input.get(key, "").strip()
                default = room_type_to_name(room_type, room_id)
                # Only save if the user changed it from the default
                if value and value != default:
                    new_names[str(room_id)] = value

            updated_options = dict(self._config_entry.options)
            updated_options[CONF_ROOM_NAMES] = new_names
            updated_options[CONF_ENABLE_CLOUD] = bool(user_input.get(CONF_ENABLE_CLOUD, False))
            # Reloads the entry (via the update listener) so the cloud session
            # is started/stopped to match the new toggle.
            return self.async_create_entry(title="", data=updated_options)

        # The cloud toggle is always offered (it doesn't depend on rooms being
        # known yet); room-name fields are added when rooms are available.
        schema_dict: dict[Any, Any] = {
            vol.Optional(CONF_ENABLE_CLOUD, default=cloud_enabled): bool
        }
        for room_id, room_type in sorted(rooms, key=lambda r: r[0]):
            default_name = room_type_to_name(room_type, room_id)
            current_name = saved_names.get(str(room_id), default_name)
            schema_dict[vol.Required(f"room_{room_id}", default=current_name)] = str

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema_dict),
            description_placeholders={
                "message": (
                    "Enable cloud to use room cleaning (logs into Eufy's cloud with "
                    "your saved credentials; required because rooms aren't available "
                    "locally). Room-name fields appear once rooms are detected."
                )
            },
        )
