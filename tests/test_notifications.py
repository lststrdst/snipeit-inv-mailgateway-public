import datetime as dt

import pytest

from snipeit_inventory_gateway import notifications
from snipeit_inventory_gateway.mail_contract import mail_subject
from snipeit_inventory_gateway.notifications import Notifier
from snipeit_inventory_gateway.queue import EventQueue


def test_computer_report_is_sent_only_when_content_changes(config, payload, monkeypatch):
    queue = EventQueue(config.queue.path)
    notifier = Notifier(config, queue)
    sent = []
    monkeypatch.setattr(notifier, "_send", lambda *args, **kwargs: sent.append(args))
    assert notifier.computer(payload, "asset_id=1;preserve") is True
    assert notifier.computer(payload, "asset_id=1;preserve") is False
    payload["inventory"]["custom_fields"]["ram"] = "32 GB"
    assert notifier.computer(payload, "asset_id=1;preserve") is True
    assert len(sent) == 2
    queue.close()


def test_weekly_report_is_sent_once_per_iso_week(config, monkeypatch):
    class Client:
        @staticmethod
        def list_assets():
            return []

    queue = EventQueue(config.queue.path)
    notifier = Notifier(config, queue)
    sent = []
    monkeypatch.setattr(notifier, "_send", lambda *args, **kwargs: sent.append(args))
    generated = dt.datetime(2026, 9, 1, 9, tzinfo=dt.UTC)
    assert notifier.weekly_inventory(Client(), generated) is True
    assert notifier.weekly_inventory(Client(), generated + dt.timedelta(days=1)) is False
    assert len(sent) == 1
    queue.close()


def test_outgoing_mail_has_fixed_route_and_loop_suppression(config, monkeypatch):
    sent = []

    class FakeSmtp:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def starttls(self, **kwargs):
            return None

        def login(self, *args):
            return None

        def send_message(self, message):
            sent.append(message)

    monkeypatch.setattr(notifications.smtplib, "SMTP", FakeSmtp)
    queue = EventQueue(config.queue.path)
    notifier = Notifier(config, queue)
    notifier._send(mail_subject("error", "IMAP · требуется внимание"), "details", category="error")
    message = sent[0]
    assert message["From"] == "notification@example.com"
    assert message["To"] == "inventory@example.com"
    assert message["Subject"] == "[SnipeIT Inventory Gateway][ERROR] IMAP · требуется внимание"
    assert message["X-SnipeIT-Category"] == "error"
    assert message["X-SnipeIT-Mail-Class"] == "notification"
    assert message["Auto-Submitted"] == "auto-generated"
    queue.close()


def test_subject_and_category_cannot_disagree(config):
    queue = EventQueue(config.queue.path)
    notifier = Notifier(config, queue)
    with pytest.raises(ValueError, match="routing category"):
        notifier._send(mail_subject("weekly-report", "2026-W36"), "body", category="error")
    queue.close()
