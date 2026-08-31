from __future__ import annotations

from urllib.parse import quote

import httpx

from . import __version__
from .config import SnipeITConfig
from .errors import PermanentProcessingError, TemporaryProcessingError
from .policy import disposition


class SnipeITClient:
    """The only component allowed to write to the local Snipe-IT API."""

    def __init__(self, config: SnipeITConfig):
        self.config = config
        self.client = httpx.Client(
            base_url=config.url,
            verify=config.verify_tls,
            timeout=config.timeout_seconds,
            headers={
                "Authorization": f"Bearer {config.api_token.get_secret_value()}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Host": config.host_header,
                "User-Agent": f"SnipeIT-Inventory-Gateway/{__version__}",
            },
        )

    def close(self) -> None:
        self.client.close()

    def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = self.client.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            raise TemporaryProcessingError(
                f"Snipe-IT transport error: {type(exc).__name__}"
            ) from exc
        if response.status_code in {408, 425, 429} or response.status_code >= 500:
            raise TemporaryProcessingError(f"Snipe-IT temporary HTTP {response.status_code}")
        if response.status_code >= 400:
            raise PermanentProcessingError(
                f"Snipe-IT rejected request with HTTP {response.status_code}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise TemporaryProcessingError("Snipe-IT returned invalid JSON") from exc

    def apply_inventory(self, payload: dict) -> str:
        serial = payload["serial_number"]
        lookup = self._request(
            "GET", self.config.inventory_endpoint.format(serial=quote(serial, safe=""))
        )
        rows = lookup.get("rows") or ([] if not lookup.get("id") else [lookup])
        if len(rows) > 1:
            raise PermanentProcessingError(
                f"expected exactly one asset for serial, got {len(rows)}"
            )
        inventory = payload["inventory"]
        custom_values = {
            handle: inventory.get("custom_fields", {})[logical]
            for logical, handle in self.config.custom_field_map.items()
            if logical in inventory.get("custom_fields", {})
        }
        if not rows:
            create = {
                "name": inventory.get("name", payload["computer_name"]),
                "serial": serial,
                "model_id": inventory.get("model_id", self.config.default_model_id),
                "status_id": inventory.get("status_id", self.config.default_status_id),
            }
            create.update(custom_values)
            created = self._request("POST", self.config.create_endpoint, json=create)
            payload_result = created.get("payload", created)
            asset_id = payload_result.get("id")
            rows = [{"id": asset_id, "assigned_to": None}]
        else:
            asset_id = rows[0].get("id")
        if not isinstance(asset_id, int):
            raise PermanentProcessingError("asset id is missing")
        allowed = {"name", "notes", "status_id", "model_id", "company_id"}
        update = {key: inventory[key] for key in allowed if key in inventory}
        update.update(custom_values)
        if not update:
            raise PermanentProcessingError("inventory update is empty")
        result = self._request(
            "PUT", self.config.update_endpoint.format(asset_id=asset_id), json=update
        )
        if result.get("status") == "error":
            raise PermanentProcessingError("Snipe-IT application error")
        requested, username = disposition(payload["identity"])
        assigned = rows[0].get("assigned_to")
        if requested == "stock" and assigned:
            self._request(
                "POST",
                self.config.checkin_endpoint.format(asset_id=asset_id),
                json={"note": "Authoritative directory disabled state"},
            )
            return f"asset_id={asset_id};stock"
        if requested == "assigned" and username:
            users = self._request(
                "GET", self.config.user_search_endpoint, params={"search": username}
            )
            exact = [
                row
                for row in users.get("rows", [])
                if str(row.get("username", "")).lower() == username
            ]
            if len(exact) != 1:
                return f"asset_id={asset_id};preserve:identity_unresolved"
            user_id = exact[0].get("id")
            if not isinstance(user_id, int):
                return f"asset_id={asset_id};preserve:identity_unresolved"
            current_id = assigned.get("id") if isinstance(assigned, dict) else None
            if current_id != user_id:
                if assigned:
                    self._request(
                        "POST",
                        self.config.checkin_endpoint.format(asset_id=asset_id),
                        json={"note": "Inventory owner transition"},
                    )
                self._request(
                    "POST",
                    self.config.checkout_endpoint.format(asset_id=asset_id),
                    json={"checkout_to_type": "user", "assigned_user": user_id},
                )
            return f"asset_id={asset_id};assigned:{username}"
        return f"asset_id={asset_id};preserve"

    def apply(self, payload: dict) -> str:
        if payload["event_type"] in {
            "inventory",
            "install_update",
            "owner_change",
            "stock_checkin",
            "offboarding",
        }:
            return self.apply_inventory(payload)
        raise PermanentProcessingError("unsupported event type")
