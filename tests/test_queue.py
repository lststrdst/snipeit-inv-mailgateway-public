from snipeit_inventory_gateway.protocol import decode_event, encode_event
from snipeit_inventory_gateway.queue import EventQueue


def test_durable_dedup_across_transports(config, envelope):
    queue = EventQueue(config.queue.path)
    event = decode_event(envelope, config, enforce_freshness=True)
    assert queue.enqueue(event, "https") == ("pending", False)
    assert queue.enqueue(event, "smtp") == ("pending", True)
    queue.close()
    reopened = EventQueue(config.queue.path)
    assert reopened.counts() == {"pending": 1}
    reopened.close()


def test_generation_and_observed_at_watermark(config, payload):
    queue = EventQueue(config.queue.path)
    current = decode_event(
        encode_event(payload, "test-1", config.key("test-1")), config, enforce_freshness=True
    )
    queue.enqueue(current, "https")
    item = queue.claim("worker", 60)
    queue.finish(item, "processed", "ok")
    older = dict(payload, event_generation=41)
    event = decode_event(
        encode_event(older, "test-1", config.key("test-1")), config, enforce_freshness=True
    )
    queue.enqueue(event, "smtp")
    item = queue.claim("worker", 60)
    assert "generation older" in queue.stale_reason(item)
    queue.close()


def test_retry_becomes_dead_letter(config, envelope):
    queue = EventQueue(config.queue.path)
    queue.enqueue(decode_event(envelope, config, enforce_freshness=True), "https")
    item = queue.claim("worker", 60)
    assert queue.retry(item, "temporary", 1, 1) == "dead_letter"
    assert queue.counts() == {"dead_letter": 1}
    queue.close()


def test_retention_purge_removes_processed_but_keeps_dead_letter(config, envelope):
    queue = EventQueue(config.queue.path)
    event = decode_event(envelope, config, enforce_freshness=True)
    queue.enqueue(event, "https")
    processed = queue.claim("worker", 60)
    queue.finish(processed, "processed", "ok")
    queue.db.execute(
        "UPDATE events SET updated_at='2000-01-01T00:00:00+00:00' WHERE event_id=?",
        (event.event_id,),
    )
    assert queue.purge(7) == 1
    assert queue.status(event.event_id) is None

    dead_payload = dict(event.payload, event_generation=event.generation + 1)
    dead_envelope = encode_event(dead_payload, "test-1", config.key("test-1"))
    dead_event = decode_event(dead_envelope, config, enforce_freshness=True)
    queue.enqueue(dead_event, "https")
    dead_item = queue.claim("worker", 60)
    assert queue.retry(dead_item, "temporary", 1, 1) == "dead_letter"
    queue.db.execute(
        "UPDATE events SET updated_at='2000-01-01T00:00:00+00:00' WHERE event_id=?",
        (dead_event.event_id,),
    )
    assert queue.purge(7) == 0
    assert queue.status(dead_event.event_id) == "dead_letter"
    queue.close()
