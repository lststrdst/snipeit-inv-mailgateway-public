from __future__ import annotations

import logging
import os
import socket
import sqlite3
import time

from .config import GatewayConfig
from .errors import PermanentProcessingError, TemporaryProcessingError
from .notifications import Notifier
from .queue import EventQueue, QueueItem
from .snipeit import SnipeITClient

LOG = logging.getLogger(__name__)


def retry_delay(config: GatewayConfig, attempts: int) -> int:
    return min(
        config.queue.retry_max_seconds,
        config.queue.retry_base_seconds * (2 ** max(0, attempts - 1)),
    )


def process_item(
    config: GatewayConfig,
    queue: EventQueue,
    client: SnipeITClient,
    notifier: Notifier,
    item: QueueItem,
) -> str:
    stale = queue.stale_reason(item)
    if stale:
        queue.finish(item, "stale", stale)
        return "stale"
    try:
        result = client.apply(item.payload)
    except PermanentProcessingError as exc:
        queue.finish(item, "rejected", str(exc))
        return "rejected"
    except TemporaryProcessingError as exc:
        status = queue.retry(
            item, str(exc), retry_delay(config, item.attempts), config.queue.max_attempts
        )
        if status == "dead_letter":
            try:
                notifier.incident(
                    f"dead:{item.event_id}",
                    "[SNIPEIT-GATEWAY] DEAD LETTER",
                    f"event_id={item.event_id}\ncomputer={item.computer_name}\nerror={exc}",
                )
            except OSError:
                LOG.exception("dead_letter_notification_failed event_id=%s", item.event_id)
        return status
    queue.finish(item, "processed", result)
    return "processed"


def run_once(
    config: GatewayConfig, queue: EventQueue | None = None, client: SnipeITClient | None = None
) -> str | None:
    own_queue = queue is None
    queue = queue or EventQueue(config.queue.path)
    client = client or SnipeITClient(config.snipeit)
    notifier = Notifier(config, queue)
    try:
        item = queue.claim(f"{socket.gethostname()}:{os.getpid()}", config.queue.lease_seconds)
        return process_item(config, queue, client, notifier, item) if item else None
    finally:
        if own_queue:
            client.close()
            queue.close()


def run_forever(config: GatewayConfig) -> None:
    queue, client = EventQueue(config.queue.path), SnipeITClient(config.snipeit)
    notifier = Notifier(config, queue)
    owner = f"{socket.gethostname()}:{os.getpid()}"
    try:
        while True:
            try:
                item = queue.claim(owner, config.queue.lease_seconds)
                if item:
                    process_item(config, queue, client, notifier, item)
                else:
                    time.sleep(2)
                notifier.recovered("queue", "durable queue")
            except sqlite3.Error as exc:
                LOG.exception("queue_error")
                try:
                    notifier.incident(
                        "queue",
                        "[SNIPEIT-GATEWAY] QUEUE ERROR",
                        f"durable queue error: {type(exc).__name__}",
                    )
                except (OSError, sqlite3.Error):
                    LOG.exception("queue_error_notification_failed")
                time.sleep(5)
    finally:
        client.close()
        queue.close()
