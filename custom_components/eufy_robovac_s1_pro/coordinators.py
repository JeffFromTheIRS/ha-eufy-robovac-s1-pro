import base64
import logging

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .tuya import Message, TuyaDevice

logger = logging.getLogger(__name__)

# DPS keys involved in room / area cleaning. When any of these change we log
# the new value at INFO (with a hex decode of the base64 payload) so it can be
# captured while triggering a single-room clean from the Eufy app — the first
# step to reverse-engineering the DPS 116 "Area Clean" command format.
ROOM_CAPTURE_DPS = {"116", "117", "124", "140", "141", "146", "147"}


def _decode_for_log(value) -> str | None:
    """Return a space-separated hex decode of a base64 DPS string, else None."""
    if isinstance(value, str) and len(value) >= 4:
        try:
            decoded = base64.b64decode(value)
        except Exception:
            return None
        return " ".join(f"{b:02x}" for b in decoded)
    return None


class EufyTuyaDataUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, *args, host: str, device_id: str, local_key: str, mac: str | None = None, **kwargs):
        super().__init__(*args, **kwargs)

        self.mac = mac
        self.tuya_client = TuyaDevice(device_id=device_id, local_key=local_key, host=host)

        extra_handler_list = [self.handle_tuya_message]

        for message_type in [Message.GET_COMMAND, Message.GRATUITOUS_UPDATE]:
            if message_type not in self.tuya_client._handlers:
                self.tuya_client._handlers[message_type] = extra_handler_list
            else:
                self.tuya_client._handlers[message_type] += extra_handler_list

    def handle_new_dps(self, new_dps: dict, async_set_updated_data_upon_change: bool = False):
        existing_dps = (self.data or {}).copy()

        changed = new_dps != existing_dps

        if changed:
            # Log which keys actually changed. Room/area DPS are logged at INFO
            # (with hex) for capture; everything else stays at DEBUG.
            for key, value in new_dps.items():
                old = existing_dps.get(key)
                if old == value:
                    continue
                if key in ROOM_CAPTURE_DPS:
                    hex_decoded = _decode_for_log(value)
                    logger.info(
                        "Room/area DPS %s changed: %r%s",
                        key,
                        value,
                        f"  (hex: {hex_decoded})" if hex_decoded else "",
                    )
                else:
                    logger.debug("DPS %s changed: %r -> %r", key, old, value)

            existing_dps.update(new_dps)

            if async_set_updated_data_upon_change:
                # only do this if there were changes as to not spam the state machine
                self.async_set_updated_data(existing_dps)

        return existing_dps

    async def handle_tuya_message(self, message, _):
        self.handle_new_dps(dict(message.payload["dps"]), async_set_updated_data_upon_change=True)

    async def _async_update_data(self):
        # note: this will call the tuya message handler above
        # which will in turn call handle_tuya_message and may cause an extra update to the state machine
        # TODO: this all needs to be cleaned up
        dps = dict((await self.tuya_client.async_get()) or {})

        return self.handle_new_dps(
            dps,
        )
