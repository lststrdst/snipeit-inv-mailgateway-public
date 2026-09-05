import pytest
from pydantic import ValidationError


def test_config_rejects_unknown_fields(config):
    raw = config.model_dump(mode="json")
    raw["debug_allow_all_routes"] = True
    with pytest.raises(ValidationError, match="Extra inputs"):
        type(config).model_validate(raw)


def test_secret_redacted(config):
    rendered = repr(config)
    assert "test-token" not in rendered and "test-imap-password" not in rendered
    assert "test-smtp-password" not in rendered


def test_mail_service_roles_must_agree(config):
    raw = config.model_dump(mode="json")
    raw["keys"][0]["master_key"] = "base64:AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
    raw["snipeit"]["api_token"] = "test-token"  # pragma: allowlist secret
    raw["imap"]["password"] = "test-imap-password"  # pragma: allowlist secret
    raw["smtp"]["password"] = "test-smtp-password"  # pragma: allowlist secret
    raw["imap"]["user"] = "other@example.com"
    with pytest.raises(ValidationError, match="notification recipient"):
        type(config).model_validate(raw)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("smtp", "user"), "personal@example.com", "SMTP authentication account"),
        (("smtp", "to_address"), "personal@example.com", "notification recipient"),
        (("imap", "allowed_from"), ["notification@example.com", "personal@example.com"], "allow-list"),
    ],
)
def test_config_rejects_personal_mailbox_routes(config, path, value, message):
    raw = config.model_dump(mode="json")
    raw["keys"][0]["master_key"] = "base64:AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
    raw["snipeit"]["api_token"] = "test-token"  # pragma: allowlist secret
    raw["imap"]["password"] = "test-imap-password"  # pragma: allowlist secret
    raw["smtp"]["password"] = "test-smtp-password"  # pragma: allowlist secret
    raw[path[0]][path[1]] = value
    with pytest.raises(ValidationError, match=message):
        type(config).model_validate(raw)


def test_imap_and_smtp_accounts_require_different_passwords(config):
    raw = config.model_dump(mode="json")
    raw["keys"][0]["master_key"] = "base64:AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
    raw["snipeit"]["api_token"] = "test-token"  # pragma: allowlist secret
    raw["imap"]["password"] = "same-test-password"  # pragma: allowlist secret
    raw["smtp"]["password"] = "same-test-password"  # pragma: allowlist secret
    with pytest.raises(ValidationError, match="different passwords"):
        type(config).model_validate(raw)


def test_non_standard_accounts_are_normalized_and_legacy_field_migrates(config):
    raw = config.model_dump(mode="json")
    raw["keys"][0]["master_key"] = "base64:AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
    raw["snipeit"]["api_token"] = "test-token"  # pragma: allowlist secret
    raw["imap"]["password"] = "test-imap-password"  # pragma: allowlist secret
    raw["smtp"]["password"] = "test-smtp-password"  # pragma: allowlist secret
    raw["ownership"]["non_standard_accounts"] = ["ExampleUser"]
    parsed = type(config).model_validate(raw)
    assert parsed.ownership.non_standard_accounts == ["exampleuser"]

    legacy = dict(raw)
    legacy["ownership"] = {**raw["ownership"], "username_exceptions": ["Legacy.User"]}
    legacy["ownership"].pop("non_standard_accounts")
    parsed_legacy = type(config).model_validate(legacy)
    assert parsed_legacy.ownership.non_standard_accounts == ["legacy.user"]


def test_production_keys_must_be_bound_to_computers(config):
    raw = config.model_dump(mode="json")
    raw["keys"][0]["master_key"] = "base64:AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
    raw["snipeit"]["api_token"] = "test-token"  # pragma: allowlist secret
    raw["imap"]["password"] = "test-imap-password"  # pragma: allowlist secret
    raw["smtp"]["password"] = "test-smtp-password"  # pragma: allowlist secret
    raw["environment"] = "production"
    with pytest.raises(ValidationError, match="allowed_computers"):
        type(config).model_validate(raw)
    raw["keys"][0]["allowed_computers"] = ["LAPTOP-TEST"]
    assert type(config).model_validate(raw).keys[0].allowed_computers == ["LAPTOP-TEST"]
