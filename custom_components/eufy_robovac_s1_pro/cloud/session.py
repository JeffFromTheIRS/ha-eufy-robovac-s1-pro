"""S1-Pro-focused orchestration of the Eufy AIOT cloud connection.

A slim replacement for eufy-clean's multi-device ``cloud.py``: log in, find the
S1 Pro in the account's device list, open the MQTT connection, track the room
list (DPS 165) from pushed messages, and send room-clean commands (DPS 152).
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from typing import Any

from .http import EufyHTTPClient
from .mqtt import EufyCleanClient
from .rooms import build_room_clean_command, parse_rooms

_LOGGER = logging.getLogger(__name__)

# Model codes for the S1 Pro (T2080 / T2080A). Match on these so an account
# with several robots (e.g. an X10 as well) still binds to the right device.
S1_PRO_MODELS = ("T2080", "T2080A")


def derive_openudid(device_id: str) -> str:
    """Stable per-device openudid for the Eufy API (no persistence needed)."""
    return hashlib.md5(f"eufy_s1_pro_{device_id}".encode()).hexdigest()


class EufyCloudSession:
    """Manages the optional cloud MQTT link for room cleaning."""

    def __init__(
        self,
        username: str,
        password: str,
        openudid: str,
        websession: Any,
        preferred_device_id: str | None = None,
    ) -> None:
        self._http = EufyHTTPClient(username, password, openudid, websession=websession)
        self._openudid = openudid
        self._preferred_device_id = preferred_device_id
        self._client: EufyCleanClient | None = None
        self._on_update: Callable[[], None] | None = None

        self.device_id: str | None = None
        self.device_model: str | None = None
        self.dps: dict[str, Any] = {}
        self.map_id: int | None = None
        self.rooms: list[dict] = []

    def set_update_callback(self, callback: Callable[[], None]) -> None:
        self._on_update = callback

    async def connect(self) -> None:
        result = await self._http.login()
        creds = result.get("mqtt")
        if not creds:
            raise RuntimeError(
                "Eufy cloud login returned no MQTT credentials — the account may "
                "need the new unified Eufy app (v2 / user_center)."
            )
        device = await self._find_device()
        if not device:
            raise RuntimeError("No S1 Pro found in the Eufy cloud device list.")
        self.device_id = device["id"]
        self.device_model = device["model"] or "T2080"
        _LOGGER.info("Eufy cloud: binding to %s (%s)", self.device_id, self.device_model)

        self._client = EufyCleanClient(
            device_id=self.device_id,
            user_id=creds["user_id"],
            app_name=creds["app_name"],
            thing_name=creds["thing_name"],
            access_key="",
            ticket="",
            openudid=self._openudid,
            certificate_pem=creds["certificate_pem"],
            private_key=creds["private_key"],
            device_model=self.device_model,
            endpoint=creds["endpoint_addr"],
        )
        self._client.set_on_message(self._handle_message)
        await self._client.connect()

    async def disconnect(self) -> None:
        if self._client:
            await self._client.disconnect()
            self._client = None

    async def _find_device(self) -> dict | None:
        devices = await self._http.get_device_list()
        if not devices:
            devices = await self._http.get_cloud_device_list()

        candidates: list[dict] = []
        for d in devices:
            did = (
                d.get("id")
                or d.get("device_sn")
                or d.get("devId")
                or d.get("deviceId")
            )
            product = d.get("product")
            model = product.get("product_code") if isinstance(product, dict) else None
            model = (
                model
                or d.get("device_model")
                or d.get("product_code")
                or d.get("model")
                or ""
            )
            if did:
                candidates.append({"id": did, "model": str(model)})

        # Prefer an S1 Pro model, then a device_id matching the local one, else first.
        for c in candidates:
            if any(m in c["model"].upper() for m in S1_PRO_MODELS):
                return c
        if self._preferred_device_id:
            for c in candidates:
                if c["id"] == self._preferred_device_id:
                    return c
        return candidates[0] if candidates else None

    def _handle_message(self, payload: bytes) -> None:
        """Runs on the event loop (client marshals it there)."""
        try:
            import json

            outer = json.loads(payload)
            inner = json.loads(outer.get("payload", "{}"))
            data = inner.get("data") or {}
            if not data:
                return
            self.dps.update(data)
            if "165" in data:
                map_id, rooms = parse_rooms(data["165"])
                if rooms:
                    self.map_id = map_id
                    self.rooms = rooms
            if self._on_update:
                self._on_update()
        except Exception as e:  # noqa: BLE001
            _LOGGER.debug("Cloud message parse error: %s", e)

    async def send_room_clean(self, room_ids: list[int], mode: int = 0) -> None:
        if not self._client:
            raise RuntimeError("Eufy cloud client is not connected.")
        map_id = self.map_id if self.map_id is not None else 0
        command = build_room_clean_command(room_ids, map_id, mode)
        _LOGGER.info(
            "Eufy cloud: room clean rooms=%s map_id=%s -> DPS152=%s",
            room_ids,
            map_id,
            command,
        )
        await self._client.send_command({"152": command})
