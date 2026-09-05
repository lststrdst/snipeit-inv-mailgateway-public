import json

from fastapi.testclient import TestClient

from snipeit_inventory_gateway import api


def write_config(config, path):
    raw = config.model_dump(mode="json")
    for key in raw["keys"]:
        key["master_key"] = "base64:AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
    raw["snipeit"]["api_token"] = "test-token"  # pragma: allowlist secret
    raw["imap"]["password"] = "test-imap-password"  # pragma: allowlist secret
    raw["smtp"]["password"] = "test-smtp-password"  # pragma: allowlist secret
    path.write_text(json.dumps(raw), encoding="utf-8")


def test_only_post_endpoint_is_exposed(config, envelope, tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    write_config(config, path)
    monkeypatch.setenv("SNIPEIT_GATEWAY_CONFIG", str(path))
    with TestClient(api.app) as client:
        response = client.post("/api/v1/events", json=envelope)
        assert response.status_code == 202
        assert response.json()["gateway_version"] == "1.0.0"
        assert client.post("/api/v1/events", json=envelope).json()["status"] == "duplicate"
        for method, route in (
            ("get", "/"),
            ("get", "/docs"),
            ("get", "/version"),
            ("post", "/api/v1/hardware"),
        ):
            assert getattr(client, method)(route).status_code == 404


def test_auth_error_is_generic(config, envelope, tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    write_config(config, path)
    monkeypatch.setenv("SNIPEIT_GATEWAY_CONFIG", str(path))
    envelope["hmac_sha256"] = "0" * 64
    with TestClient(api.app) as client:
        response = client.post("/api/v1/events", json=envelope)
        assert response.status_code == 401
        assert response.json()["error"] == "authentication_failed"
