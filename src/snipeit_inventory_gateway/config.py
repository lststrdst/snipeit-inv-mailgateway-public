from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class KeyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    key_id: str = Field(pattern=r"^[A-Za-z0-9._-]{1,64}$")
    master_key: SecretStr
    decrypt_only: bool = False
    allowed_computers: list[str] = Field(default_factory=list)

    @field_validator("master_key")
    @classmethod
    def random_base64_key(cls, value: SecretStr) -> SecretStr:
        import base64

        raw = value.get_secret_value()
        if not raw.startswith("base64:"):
            raise ValueError("master_key must use base64: encoding")
        try:
            decoded = base64.b64decode(raw[7:], validate=True)
        except Exception as exc:
            raise ValueError("master_key is not valid base64") from exc
        if len(decoded) != 32:
            raise ValueError("master_key must decode to exactly 32 random bytes")
        return value

    @field_validator("allowed_computers")
    @classmethod
    def normalize_allowed_computers(cls, value: list[str]) -> list[str]:
        import re

        normalized = []
        for computer in value:
            item = computer.strip().upper()
            if not re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,254}", item):
                raise ValueError("allowed_computers contains an invalid computer name")
            normalized.append(item)
        if len(normalized) != len(set(normalized)):
            raise ValueError("allowed_computers must not contain duplicates")
        return normalized


class QueueConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: Path = Path("/var/lib/snipeit-inventory-gateway/events.sqlite3")
    max_attempts: int = Field(12, ge=1, le=100)
    lease_seconds: int = Field(300, ge=10, le=3600)
    retry_base_seconds: int = Field(30, ge=1, le=3600)
    retry_max_seconds: int = Field(3600, ge=10, le=86400)
    retention_days: int = Field(365, ge=7, le=3650)


class ApiConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bind_host: str = "127.0.0.1"
    bind_port: int = Field(8787, ge=1024, le=65535)
    max_body_bytes: int = Field(262144, ge=4096, le=2097152)
    max_clock_skew_seconds: int = Field(900, ge=30, le=86400)


class SnipeITConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = "https://127.0.0.1"
    host_header: str = "snipeit.example.com"
    api_token: SecretStr
    verify_tls: bool = False
    timeout_seconds: int = Field(30, ge=2, le=120)
    inventory_endpoint: str = "/api/v1/hardware/byserial/{serial}"
    create_endpoint: str = "/api/v1/hardware"
    update_endpoint: str = "/api/v1/hardware/{asset_id}"
    user_search_endpoint: str = "/api/v1/users"
    checkin_endpoint: str = "/api/v1/hardware/{asset_id}/checkin"
    checkout_endpoint: str = "/api/v1/hardware/{asset_id}/checkout"
    default_model_id: int = Field(1, ge=1)
    default_status_id: int = Field(8, ge=1)
    custom_field_map: dict[str, str] = Field(default_factory=dict)

    @field_validator("custom_field_map")
    @classmethod
    def validate_custom_fields(cls, value: dict[str, str]) -> dict[str, str]:
        for logical, handle in value.items():
            if not logical or not handle.startswith("_snipeit_"):
                raise ValueError("custom field map must use logical names and Snipe-IT handles")
        return value

    @field_validator(
        "inventory_endpoint",
        "create_endpoint",
        "update_endpoint",
        "user_search_endpoint",
        "checkin_endpoint",
        "checkout_endpoint",
    )
    @classmethod
    def fixed_local_paths(cls, value: str) -> str:
        if not value.startswith("/api/v1/") or ".." in value or "?" in value:
            raise ValueError("Snipe-IT endpoint must be a fixed /api/v1 path")
        return value


class ImapConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = "imap.example.com"
    port: int = 993
    user: str = "inventory@example.com"
    password: SecretStr
    inbox: str = "INBOX"
    parent_folder: str = "SnipeIT Inventory"
    allowed_from: list[str] = Field(default_factory=list)
    max_messages_per_run: int = Field(200, ge=1, le=1000)
    max_message_bytes: int = Field(2097152, ge=4096, le=10485760)
    terminal_wait_seconds: int = Field(45, ge=1, le=300)


class SmtpConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = "smtp.example.com"
    port: int = 587
    user: str = "notification@example.com"
    password: SecretStr
    from_address: str = "notification@example.com"
    to_address: str = "inventory@example.com"
    starttls: Literal[True] = True


class NotificationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    throttle_seconds: int = Field(3600, ge=60, le=86400)
    weekly_weekday: int = Field(0, ge=0, le=6)
    weekly_hour: int = Field(9, ge=0, le=23)


class GatewayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    environment: Literal["development", "staging", "production"] = "development"
    keys: list[KeyConfig] = Field(min_length=1)
    queue: QueueConfig = Field(default_factory=QueueConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    snipeit: SnipeITConfig
    imap: ImapConfig
    smtp: SmtpConfig
    notifications: NotificationConfig = Field(default_factory=NotificationConfig)

    @model_validator(mode="after")
    def unique_keys_and_accounts(self) -> GatewayConfig:
        ids = [key.key_id for key in self.keys]
        if len(ids) != len(set(ids)):
            raise ValueError("key_id values must be unique")
        if self.imap.user.lower() != self.smtp.to_address.lower():
            raise ValueError("IMAP user must match the SMTP notification recipient")
        if self.smtp.user.lower() != self.smtp.from_address.lower():
            raise ValueError("SMTP login must match the notification sender")
        if self.environment == "production" and any(not key.allowed_computers for key in self.keys):
            raise ValueError("production ingest keys must be bound to allowed_computers")
        return self

    def key_config(self, key_id: str) -> KeyConfig:
        for item in self.keys:
            if item.key_id == key_id:
                return item
        raise KeyError(key_id)

    def key(self, key_id: str) -> bytes:
        import base64

        item = self.key_config(key_id)
        return base64.b64decode(item.master_key.get_secret_value()[7:], validate=True)


def load_config(path: str | Path | None = None) -> GatewayConfig:
    resolved = Path(
        path or os.environ.get("SNIPEIT_GATEWAY_CONFIG", "/etc/snipeit-inventory-gateway/config.json")
    )
    data = json.loads(resolved.read_text(encoding="utf-8"))
    return GatewayConfig.model_validate(data)
