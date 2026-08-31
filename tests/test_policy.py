import pytest

from snipeit_inventory_gateway.policy import disposition, safe_owner
from snipeit_inventory_gateway.snipeit import SnipeITClient


@pytest.mark.parametrize(
    "username",
    ["SYSTEM", "shared-terminal", "svc_backup", "service_sync", "ad_join", "DWM-1", "krbtgt"],
)
def test_system_and_service_identities_never_become_owner(username):
    assert safe_owner({"detected_username": username}) is None


def test_endpoint_offboarding_hints_are_never_authoritative():
    assert disposition({"detected_username": "demo.user", "ou_terminated_hint": True})[0] == "assigned"
    assert (
        disposition({"detected_username": "demo.user", "description_terminated_hint": True})[0]
        == "assigned"
    )
    assert disposition({"detected_username": "demo.user", "authoritative_disabled": True}) == (
        "assigned",
        "demo.user",
    )


class FakeClient(SnipeITClient):
    def __init__(self, config, responses):
        self.config = config
        self.responses = iter(responses)
        self.calls = []

    def _request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        return next(self.responses)


def test_assignment_uses_exact_user_and_fixed_checkout_endpoint(config, payload):
    payload["identity"] = {"detected_username": "DOMAIN\\demo.user"}
    client = FakeClient(
        config.snipeit,
        [
            {"rows": [{"id": 55, "assigned_to": None}]},
            {"status": "success"},
            {"rows": [{"id": 77, "username": "demo.user"}]},
            {"status": "success"},
        ],
    )
    assert client.apply(payload) == "asset_id=55;assigned:demo.user"
    assert client.calls[-1][0:2] == ("POST", "/api/v1/hardware/55/checkout")
    assert client.calls[-1][2]["json"]["assigned_user"] == 77


def test_endpoint_cannot_force_checkin(config, payload):
    payload["identity"] = {"detected_username": "demo.user", "authoritative_disabled": True}
    client = FakeClient(
        config.snipeit,
        [
            {"rows": [{"id": 55, "assigned_to": {"id": 77}}]},
            {"status": "success"},
            {"rows": [{"id": 77, "username": "demo.user"}]},
        ],
    )
    assert client.apply(payload) == "asset_id=55;assigned:demo.user"
    assert not any(call[1].endswith("/checkin") for call in client.calls)


def test_unresolved_identity_updates_inventory_but_preserves_assignment(config, payload):
    payload["identity"] = {"detected_username": "svc_bad"}
    client = FakeClient(
        config.snipeit,
        [{"rows": [{"id": 55, "assigned_to": {"id": 77}}]}, {"status": "success"}],
    )
    assert client.apply(payload) == "asset_id=55;preserve"
    assert len(client.calls) == 2


def test_missing_asset_is_created_without_asset_tag(config, payload):
    payload["identity"] = {"detected_username": ""}
    client = FakeClient(
        config.snipeit,
        [{"rows": []}, {"payload": {"id": 55}}, {"status": "success"}],
    )
    assert client.apply(payload) == "asset_id=55;preserve"
    create_call = client.calls[1]
    assert create_call[0:2] == ("POST", "/api/v1/hardware")
    assert create_call[2]["json"]["serial"] == "SERIAL-001"
    assert "asset_tag" not in create_call[2]["json"]


def test_serial_cannot_escape_fixed_lookup_path(config, payload):
    payload["serial_number"] = "../users?admin=true"
    payload["identity"] = {"detected_username": ""}
    client = FakeClient(
        config.snipeit,
        [{"rows": [{"id": 55, "assigned_to": None}]}, {"status": "success"}],
    )
    client.apply(payload)
    assert client.calls[0][1] == "/api/v1/hardware/byserial/..%2Fusers%3Fadmin%3Dtrue"
