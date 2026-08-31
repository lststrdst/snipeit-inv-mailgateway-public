import datetime as dt
import json

import pytest

from snipeit_inventory_gateway.errors import AuthenticationError, ValidationError
from snipeit_inventory_gateway.protocol import decode_event, encode_event


def test_round_trip_encrypts_sensitive_inventory(config, payload, envelope):
    raw = json.dumps(envelope).encode()
    assert b"SERIAL-001" not in raw and b"demo.user" not in raw
    decoded = decode_event(raw, config, enforce_freshness=True)
    assert decoded.payload["serial_number"] == "SERIAL-001"


def test_ciphertext_tamper_rejected_before_decryption(config, envelope):
    changed = dict(envelope)
    changed["ciphertext"] = ("A" if changed["ciphertext"][0] != "A" else "B") + changed[
        "ciphertext"
    ][1:]
    with pytest.raises(AuthenticationError):
        decode_event(changed, config, enforce_freshness=True)


def test_event_id_binding_and_unknown_fields(config, payload):
    payload["unexpected"] = "path injection"
    with pytest.raises(ValidationError, match="unknown"):
        encode_event(payload, "test-1", config.key("test-1"))


def test_https_freshness_but_smtp_replay(config, payload):
    old = dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)
    envelope = encode_event(payload, "test-1", config.key("test-1"), sent_at=old)
    with pytest.raises(AuthenticationError, match="stale"):
        decode_event(envelope, config, enforce_freshness=True)
    assert decode_event(envelope, config, enforce_freshness=False).computer_name == "LAPTOP-TEST"


def test_unknown_key_is_generic_authentication_failure(config, envelope):
    envelope["key_id"] = "retired-key"
    with pytest.raises(AuthenticationError):
        decode_event(envelope, config, enforce_freshness=True)


def test_key_can_be_bound_to_enrolled_computer(config, payload):
    key = type(config.keys[0]).model_validate(
        {
            "key_id": "test-1",
            "master_key": "base64:AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=",
            "allowed_computers": ["OTHER-PC"],
        }
    )
    bound = config.model_copy(update={"keys": [key]})
    envelope = encode_event(payload, "test-1", bound.key("test-1"))
    with pytest.raises(AuthenticationError, match="not authorized"):
        decode_event(envelope, bound, enforce_freshness=True)


def test_decrypt_only_key_rejects_live_https_but_accepts_offline_queue(config, payload):
    key = type(config.keys[0]).model_validate(
        {
            "key_id": "test-1",
            "master_key": "base64:AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=",
            "decrypt_only": True,
        }
    )
    retired = config.model_copy(update={"keys": [key]})
    envelope = encode_event(payload, "test-1", retired.key("test-1"))
    with pytest.raises(AuthenticationError, match="retired"):
        decode_event(envelope, retired, enforce_freshness=True)
    assert decode_event(envelope, retired, enforce_freshness=False).computer_name == "LAPTOP-TEST"
