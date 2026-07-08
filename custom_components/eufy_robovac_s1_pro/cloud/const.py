"""Eufy AIOT cloud endpoint constants (from jeppesens/eufy-clean)."""

from __future__ import annotations

from typing import Final

EUFY_API_BASE_URL: Final = "https://api.eufylife.com"
EUFY_HOME_API_BASE_URL: Final = "https://home-api.eufylife.com"
EUFY_AIOT_API_BASE_URL: Final = "https://aiot-clean-api-pr.eufylife.com"

EUFY_API_LOGIN: Final = f"{EUFY_HOME_API_BASE_URL}/v1/user/email/login"
EUFY_API_LOGIN_V2: Final = f"{EUFY_HOME_API_BASE_URL}/v1/user/v2/email/login"
EUFY_API_USER_INFO: Final = f"{EUFY_API_BASE_URL}/v1/user/user_center_info"
EUFY_API_DEVICE_LIST: Final = (
    f"{EUFY_AIOT_API_BASE_URL}/app/devicerelation/get_device_list"
)
EUFY_API_DEVICE_V2: Final = f"{EUFY_API_BASE_URL}/v1/device/v2"
EUFY_API_DEVICE_LIST_HOME: Final = f"{EUFY_HOME_API_BASE_URL}/v1/device/"
EUFY_API_MQTT_INFO: Final = (
    f"{EUFY_AIOT_API_BASE_URL}/app/devicemanage/get_user_mqtt_info"
)
