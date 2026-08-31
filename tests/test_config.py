import pytest
from pydantic import ValidationError


def test_config_rejects_unknown_fields(config):
    raw = config.model_dump(mode="json")
    raw["debug_allow_all_routes"] = True
    with pytest.raises(ValidationError, match="Extra inputs"):
        type(config).model_validate(raw)


def test_secret_redacted(config):
    rendered = repr(config)
    assert "test-token" not in rendered and "test-password" not in rendered


def test_mail_accounts_must_follow_configured_relationships(config):
    raw = config.model_dump(mode="json")
    raw["keys"][0]["master_key"] = "base64:AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
    raw["snipeit"]["api_token"] = "test-token"  # pragma: allowlist secret
    raw["imap"]["password"] = "test-password"  # pragma: allowlist secret
    raw["smtp"]["password"] = "test-password"  # pragma: allowlist secret
    raw["imap"]["user"] = "other@example.com"
    with pytest.raises(ValidationError, match="must match"):
        type(config).model_validate(raw)


def test_production_keys_must_be_bound_to_computers(config):
    raw = config.model_dump(mode="json")
    raw["keys"][0]["master_key"] = "base64:AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
    raw["snipeit"]["api_token"] = "test-token"  # pragma: allowlist secret
    raw["imap"]["password"] = "test-password"  # pragma: allowlist secret
    raw["smtp"]["password"] = "test-password"  # pragma: allowlist secret
    raw["environment"] = "production"
    with pytest.raises(ValidationError, match="allowed_computers"):
        type(config).model_validate(raw)
    raw["keys"][0]["allowed_computers"] = ["LAPTOP-TEST"]
    assert type(config).model_validate(raw).keys[0].allowed_computers == ["LAPTOP-TEST"]
