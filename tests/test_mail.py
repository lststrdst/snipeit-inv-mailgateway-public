import json
from email.message import EmailMessage

from snipeit_inventory_gateway.mail import (
    FOLDERS,
    classify,
    decoded_subject,
    encrypted_attachment,
    folder_path,
    imap_mailbox,
)


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
    message = make("[SnipeIT Inventory Gateway][RELAY] LAPTOP-TEST - event 0123", envelope=envelope)
    message["X-SnipeIT-Mail-Class"] = "transport"
    assert classify(message, ["notification@example.com"], "inventory@example.com") == "relay"


def test_fixed_human_priority_and_legacy_compatibility():
    allowed = ["notification@example.com"]
    assert classify(make("[PCINV-REPORT] WEEKLY ERROR WARNING ALERT"), allowed, "inventory@example.com") == "legacy"
    assert classify(make("PC Inventory WARNING ALERT REPORT"), allowed, "inventory@example.com") == "legacy"
    assert classify(make("[PCINV-ALERT] machine"), allowed, "inventory@example.com") == "legacy"


def test_mime_subject_is_decoded_locally():
    message = make("Предупреждение")
    raw = message.as_bytes()
    reparsed = __import__("email").message_from_bytes(raw)
    assert decoded_subject(reparsed) == "Предупреждение"


def test_unrelated_and_spoofed_relay_untouched(envelope):
    assert classify(make("ordinary message"), ["notification@example.com"], "inventory@example.com") is None
    spoof = make("[SNIPEIT-INVENTORY] RELAY: PC", sender="attacker@example.com", envelope=envelope)
    assert classify(spoof, ["notification@example.com"], "inventory@example.com") is None


def test_personal_or_copied_recipient_is_never_touched(envelope):
    personal = make("[SnipeIT Inventory Gateway][RELAY] LAPTOP-TEST", envelope=envelope)
    personal.replace_header("To", "personal@example.com")
    personal["X-SnipeIT-Mail-Class"] = "transport"
    assert classify(personal, ["notification@example.com"], "inventory@example.com") is None

    copied = make("[SnipeIT Inventory Gateway][REPORT] LAPTOP-TEST")
    copied["Cc"] = "personal@example.com"
    copied["X-SnipeIT-Category"] = "computer-report"
    assert classify(copied, ["notification@example.com"], "inventory@example.com") is None


def test_attachment_contains_no_plain_inventory(envelope, payload):
    message = make("[SNIPEIT-INVENTORY] RELAY: LAPTOP-TEST", envelope=envelope)
    raw = message.as_bytes()
    assert payload["serial_number"].encode() not in raw
    assert json.loads(encrypted_attachment(message))["event_id"] == envelope["event_id"]


def test_generated_russian_categories_and_unicode_folders(config):
    message = make("[SnipeIT Inventory Gateway][WEEKLY] 2026-W36 · критично 0 из 2")
    message["X-SnipeIT-Category"] = "weekly-report"
    assert classify(message, ["notification@example.com"], "inventory@example.com") == "weekly"
    encoded = imap_mailbox(folder_path(config, "weekly"))
    assert encoded == '"SnipeIT Inventory/Weekly Reports"'


def test_only_three_clear_destination_folders(config):
    assert {folder_path(config, key) for key in FOLDERS} == {
        "SnipeIT Inventory/Reports",
        "SnipeIT Inventory/Weekly Reports",
        "SnipeIT Inventory/Errors",
    }
