from snipeit_inventory_gateway.errors import (
    PermanentProcessingError,
    TemporaryProcessingError,
)
from snipeit_inventory_gateway.protocol import decode_event
from snipeit_inventory_gateway.queue import EventQueue
from snipeit_inventory_gateway.worker import process_item


class FakeClient:
    def __init__(self, outcome):
        self.outcome = outcome

    def apply(self, payload, owner_username=None):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class FakeNotifier:
    def __init__(self):
        self.incidents = []

    def incident(self, *args):
        self.incidents.append(args)

    def computer(self, *args):
        return True

    def owner_change(self, *args):
        return True


def claimed(config, envelope):
    queue = EventQueue(config.queue.path)
    queue.enqueue(decode_event(envelope, config, enforce_freshness=True), "https")
    return queue, queue.claim("test", 60)


def test_success_applies_watermark(config, envelope):
    queue, item = claimed(config, envelope)
    assert process_item(config, queue, FakeClient("updated"), FakeNotifier(), item) == "processed"
    assert queue.status(item.event_id) == "processed"
    assert queue.db.execute("SELECT count(*) FROM watermarks").fetchone()[0] == 1
    queue.close()


def test_permanent_error_is_rejected(config, envelope):
    queue, item = claimed(config, envelope)
    notifier = FakeNotifier()
    result = process_item(
        config, queue, FakeClient(PermanentProcessingError("bad asset")), notifier, item
    )
    assert result == "rejected" and queue.status(item.event_id) == "rejected"
    assert len(notifier.incidents) == 1
    assert "окончательный отказ" in notifier.incidents[0][2]
    queue.close()


def test_temporary_error_reports_safe_retry_details(config, envelope):
    config.queue.max_attempts = 3
    queue, item = claimed(config, envelope)
    notifier = FakeNotifier()
    result = process_item(
        config, queue, FakeClient(TemporaryProcessingError("Snipe-IT unavailable")), notifier, item
    )
    assert result == "retry"
    assert "будет повтор" in notifier.incidents[0][2]
    queue.close()


def test_temporary_error_retries_then_dead_letters_with_notification(config, envelope):
    config.queue.max_attempts = 1
    queue, item = claimed(config, envelope)
    notifier = FakeNotifier()
    result = process_item(
        config, queue, FakeClient(TemporaryProcessingError("offline")), notifier, item
    )
    assert result == "dead_letter"
    assert len(notifier.incidents) == 1
    queue.close()
