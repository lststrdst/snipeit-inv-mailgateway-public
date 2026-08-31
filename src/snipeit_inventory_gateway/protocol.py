from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives import hashes, padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .config import GatewayConfig
from .errors import AuthenticationError, ValidationError

EVENT_ID_RE = re.compile(r"^[0-9a-f]{64}$")
ENVELOPE_FIELDS = {
    "version",
    "algorithm",
    "key_id",
    "event_id",
    "sent_at",
    "salt",
    "iv",
    "ciphertext",
    "hmac_sha256",
}
PAYLOAD_FIELDS = {
    "schema_version",
    "event_id",
    "event_type",
    "event_generation",
    "observed_at",
    "computer_name",
    "serial_number",
    "identity",
    "inventory",
    "agent",
}


@dataclass(frozen=True)
class DecodedEvent:
    event_id: str
    event_type: str
    computer_name: str
    generation: int
    observed_at: dt.datetime
    key_id: str
    payload: dict[str, Any]
    envelope: dict[str, Any]


def utc(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValidationError("invalid RFC3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValidationError("timestamp must include timezone")
    return parsed.astimezone(dt.UTC)


def canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def canonical_event_id(payload: Mapping[str, Any]) -> str:
    material = dict(payload)
    material.pop("event_id", None)
    return hashlib.sha256(canonical_json(material)).hexdigest()


def validate_payload(payload: Mapping[str, Any]) -> None:
    if set(payload) - PAYLOAD_FIELDS:
        raise ValidationError("unknown payload fields")
    required = PAYLOAD_FIELDS
    missing = required - set(payload)
    if missing:
        raise ValidationError(f"missing payload fields: {','.join(sorted(missing))}")
    if payload["schema_version"] != 1:
        raise ValidationError("unsupported payload schema")
    if payload["event_type"] not in {
        "inventory",
        "install_update",
        "owner_change",
        "stock_checkin",
        "offboarding",
    }:
        raise ValidationError("unsupported event_type")
    if not isinstance(payload["event_generation"], int) or payload["event_generation"] < 0:
        raise ValidationError("invalid event_generation")
    for field, maximum in (("computer_name", 255), ("serial_number", 255)):
        value = payload[field]
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValidationError(f"invalid {field}")
    if not isinstance(payload["identity"], dict) or not isinstance(payload["inventory"], dict):
        raise ValidationError("identity and inventory must be objects")
    if not isinstance(payload["agent"], dict):
        raise ValidationError("agent must be an object")
    utc(str(payload["observed_at"]))
    event_id = str(payload["event_id"])
    if not EVENT_ID_RE.fullmatch(event_id) or not hmac.compare_digest(
        event_id, canonical_event_id(payload)
    ):
        raise ValidationError("event_id is not canonical")


def derive_keys(master_key: bytes, salt: bytes) -> tuple[bytes, bytes]:
    material = HKDF(
        algorithm=hashes.SHA256(),
        length=64,
        salt=salt,
        info=b"snipeit-inventory-gateway/v1",
    ).derive(master_key)
    return material[:32], material[32:]


def signature_input(envelope: Mapping[str, Any]) -> bytes:
    signed = {key: envelope[key] for key in ENVELOPE_FIELDS - {"hmac_sha256"}}
    return canonical_json(signed)


def encode_event(
    payload: dict[str, Any], key_id: str, master_key: bytes, *, sent_at: dt.datetime | None = None
) -> dict[str, Any]:
    material = dict(payload)
    material["event_id"] = canonical_event_id(material)
    validate_payload(material)
    salt, iv = os.urandom(32), os.urandom(16)
    encryption_key, mac_key = derive_keys(master_key, salt)
    padder = padding.PKCS7(128).padder()
    padded = padder.update(canonical_json(material)) + padder.finalize()
    encryptor = Cipher(algorithms.AES(encryption_key), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    timestamp = (sent_at or dt.datetime.now(dt.UTC)).astimezone(dt.UTC)
    envelope: dict[str, Any] = {
        "version": 1,
        "algorithm": "AES-256-CBC+HMAC-SHA256",
        "key_id": key_id,
        "event_id": material["event_id"],
        "sent_at": timestamp.isoformat().replace("+00:00", "Z"),
        "salt": base64.b64encode(salt).decode(),
        "iv": base64.b64encode(iv).decode(),
        "ciphertext": base64.b64encode(ciphertext).decode(),
    }
    envelope["hmac_sha256"] = hmac.new(
        mac_key, signature_input(envelope), hashlib.sha256
    ).hexdigest()
    return envelope


def decode_event(
    raw: bytes | str | Mapping[str, Any], config: GatewayConfig, *, enforce_freshness: bool
) -> DecodedEvent:
    try:
        envelope = dict(raw if isinstance(raw, Mapping) else json.loads(raw))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValidationError("invalid envelope JSON") from exc
    if set(envelope) != ENVELOPE_FIELDS:
        raise ValidationError("invalid envelope fields")
    if envelope["version"] != 1 or envelope["algorithm"] != "AES-256-CBC+HMAC-SHA256":
        raise ValidationError("unsupported envelope")
    try:
        key_config = config.key_config(str(envelope["key_id"]))
        master_key = config.key(str(envelope["key_id"]))
    except KeyError as exc:
        raise AuthenticationError("unknown key") from exc
    if key_config.decrypt_only and enforce_freshness:
        raise AuthenticationError("retired key is not accepted for live HTTPS ingest")
    try:
        salt = base64.b64decode(envelope["salt"], validate=True)
        iv = base64.b64decode(envelope["iv"], validate=True)
        ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
    except Exception as exc:
        raise ValidationError("invalid transport encoding") from exc
    if len(salt) != 32 or len(iv) != 16 or not ciphertext or len(ciphertext) % 16:
        raise ValidationError("invalid cryptographic lengths")
    encryption_key, mac_key = derive_keys(master_key, salt)
    expected = hmac.new(mac_key, signature_input(envelope), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, str(envelope["hmac_sha256"])):
        raise AuthenticationError("authentication failed")
    sent_at = utc(str(envelope["sent_at"]))
    if enforce_freshness:
        age = abs((dt.datetime.now(dt.UTC) - sent_at).total_seconds())
        if age > config.api.max_clock_skew_seconds:
            raise AuthenticationError("stale transport envelope")
    try:
        decryptor = Cipher(algorithms.AES(encryption_key), modes.CBC(iv)).decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        plaintext = unpadder.update(padded) + unpadder.finalize()
        payload = json.loads(plaintext)
    except Exception as exc:
        raise AuthenticationError("decryption failed") from exc
    if not isinstance(payload, dict):
        raise ValidationError("payload must be an object")
    validate_payload(payload)
    if key_config.allowed_computers and payload["computer_name"].upper() not in set(
        key_config.allowed_computers
    ):
        raise AuthenticationError("key is not authorized for this computer")
    if not hmac.compare_digest(str(envelope["event_id"]), str(payload["event_id"])):
        raise AuthenticationError("event binding failed")
    return DecodedEvent(
        event_id=payload["event_id"],
        event_type=payload["event_type"],
        computer_name=payload["computer_name"],
        generation=payload["event_generation"],
        observed_at=utc(payload["observed_at"]),
        key_id=envelope["key_id"],
        payload=payload,
        envelope=envelope,
    )
