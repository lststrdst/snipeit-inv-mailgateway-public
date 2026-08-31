from __future__ import annotations

import datetime as dt

import pytest

from snipeit_inventory_gateway.config import GatewayConfig
from snipeit_inventory_gateway.protocol import encode_event

MASTER = bytes(range(32))


@pytest.fixture
def config(tmp_path):
    return GatewayConfig.model_validate(
        {
            "schema_version": 1,
            "environment": "staging",
            "keys": [
                {
                    "key_id": "test-1",
                    "master_key": "base64:AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=",
                }
            ],
            "queue": {
                "path": str(tmp_path / "events.sqlite3"),
                "retry_base_seconds": 1,
                "retry_max_seconds": 10,
            },
            "api": {"max_clock_skew_seconds": 900},
        "snipeit": {"api_token": "test-token"},  # pragma: allowlist secret
            "imap": {
            "password": "test-password",  # pragma: allowlist secret
                "allowed_from": ["notification@example.com"],
                "terminal_wait_seconds": 1,
            },
        "smtp": {"password": "test-password"},  # pragma: allowlist secret
        }
    )


@pytest.fixture
def payload():
    return {
        "schema_version": 1,
        "event_id": "",
        "event_type": "inventory",
        "event_generation": 42,
        "observed_at": dt.datetime.now(dt.UTC).isoformat(),
        "computer_name": "LAPTOP-TEST",
        "serial_number": "SERIAL-001",
        "identity": {"detected_username": "demo.user"},
        "inventory": {"name": "LAPTOP-TEST", "custom_fields": {"ram": "16 GB"}},
        "agent": {"name": "SnipeIT Inventory Agent", "version": "1.0.0"},
    }


@pytest.fixture
def envelope(payload):
    return encode_event(payload, "test-1", MASTER)
