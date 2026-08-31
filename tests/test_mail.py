import json
from email.message import EmailMessage

from snipeit_inventory_gateway.mail import classify, decoded_subject, encrypted_attachment


def make(subject, sender="notification@example.com", envelope=None):
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "inventory@example.com"
    message["Subject"] = subject
    message.set_content("body")
    if envelope is not None:
        message["X-SnipeIT-Relay"] = "1"
        message.add_attachment(
            json.dumps(envelope).encode(),
            maintype="application",
            subtype="json",
            filename="event.snipeit-event.json",
        )
    return message


def test_relay_has_fixed_highest_priority(envelope):
    message = make("[SNIPEIT-INVENTORY] RELAY: ERROR ALERT WEEKLY", envelope=envelope)
    assert classify(message, ["notification@example.com"]) == "relay"


def test_fixed_human_priority_and_legacy_compatibility():
    allowed = ["notification@example.com"]
    assert classify(make("[PCINV-REPORT] WEEKLY ERROR WARNING ALERT"), allowed) == "weekly"
    assert classify(make("PC Inventory WARNING ALERT REPORT"), allowed) == "warning"
    assert classify(make("[PCINV-ALERT] machine"), allowed) == "alert"


def test_mime_subject_is_decoded_locally():
    message = make("Предупреждение")
    raw = message.as_bytes()
    reparsed = __import__("email").message_from_bytes(raw)
    assert decoded_subject(reparsed) == "Предупреждение"


def test_unrelated_and_spoofed_relay_untouched(envelope):
    assert classify(make("ordinary message"), ["notification@example.com"]) is None
    spoof = make("[SNIPEIT-INVENTORY] RELAY: PC", sender="attacker@example.com", envelope=envelope)
    assert classify(spoof, ["notification@example.com"]) is None


def test_attachment_contains_no_plain_inventory(envelope, payload):
    message = make("[SNIPEIT-INVENTORY] RELAY: LAPTOP-TEST", envelope=envelope)
    raw = message.as_bytes()
    assert payload["serial_number"].encode() not in raw
    assert json.loads(encrypted_attachment(message))["event_id"] == envelope["event_id"]
