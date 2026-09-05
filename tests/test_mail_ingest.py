import json
from email.message import EmailMessage

from snipeit_inventory_gateway.errors import TemporaryProcessingError
from snipeit_inventory_gateway.mail import ingest_message
from snipeit_inventory_gateway.queue import EventQueue


class Client:
    def __init__(self, outcome):
        self.outcome = outcome

    def apply(self, payload, owner_username=None):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class Notifier:
    def incident(self, *args):
        return None

    def computer(self, *args):
        return True

    def owner_change(self, *args):
        return True


def raw_mail(envelope):
    message = EmailMessage()
    message["From"] = "notification@example.com"
    message["To"] = "inventory@example.com"
    message["Subject"] = "[SNIPEIT-INVENTORY] RELAY: LAPTOP-TEST"
    message["X-SnipeIT-Relay"] = "1"
    message.set_content("encrypted fallback")
    message.add_attachment(
        json.dumps(envelope).encode(),
        maintype="application",
        subtype="json",
        filename="event.snipeit-event.json",
    )
    return message.as_bytes()


def test_smtp_event_completes_in_same_pass(config, envelope):
    queue = EventQueue(config.queue.path)
    assert (
        ingest_message(config, queue, Client("ok"), Notifier(), raw_mail(envelope)) == "processed"
    )
    assert queue.counts() == {"processed": 1}
    queue.close()


def test_smtp_temporary_error_routes_offline_relay(config, envelope):
    queue = EventQueue(config.queue.path)
    route = ingest_message(
        config,
        queue,
        Client(TemporaryProcessingError("offline")),
        Notifier(),
        raw_mail(envelope),
    )
    assert route == "error" and queue.counts() == {"retry": 1}
    queue.close()


def test_smtp_duplicate_processed_is_processed(config, envelope):
    queue = EventQueue(config.queue.path)
    raw = raw_mail(envelope)
    assert ingest_message(config, queue, Client("ok"), Notifier(), raw) == "processed"
    assert ingest_message(config, queue, Client("must-not-run"), Notifier(), raw) == "processed"
    assert queue.counts() == {"processed": 1}
    queue.close()
