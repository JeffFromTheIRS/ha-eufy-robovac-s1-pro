from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN


class CoordinatorTuyaDeviceUniqueIDMixin:
    _attr_has_entity_name = True

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.tuya_client.device_id)},
            manufacturer="Eufy",
            name="Eufy Robovac S1 Pro",
            model="S1 Pro (T2080)",
        )

    @property
    def unique_id(self) -> str:
        slug = self.name.lower().replace(" ", "_")

        return self.coordinator.tuya_client.device_id + "-" + slug
