from __future__ import annotations

import logging
import os
import socket
import sqlite3
import time
from copy import deepcopy

from .config import GatewayConfig
from .errors import PermanentProcessingError, TemporaryProcessingError
from .notifications import Notifier
from .policy import safe_owner
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
        payload = deepcopy(item.payload)
        proposed = safe_owner(
            payload.get("identity") or {},
            config.ownership.username_pattern,
            config.ownership.non_standard_accounts,
        )
        decision = queue.decide_owner(
            item.computer_name,
            proposed,
            item.observed_at,
            item.event_id,
            config.ownership.confirmation_events,
            config.ownership.confirmation_hours,
            config.ownership.candidate_window_days,
        )
        result = client.apply(payload, owner_username=decision.username)
    except PermanentProcessingError as exc:
        queue.finish(item, "rejected", str(exc))
        try:
            notifier.incident(
                f"rejected:{item.event_id}",
                f"Обработка события {item.computer_name}",
                f"Идентификатор: {item.event_id}\nКомпьютер: {item.computer_name}"
                f"\nРезультат: окончательный отказ\nОшибка: {str(exc)[:500]}",
            )
        except Exception:
            LOG.exception("rejected_notification_failed event_id=%s", item.event_id)
        return "rejected"
    except TemporaryProcessingError as exc:
        status = queue.retry(
            item, str(exc), retry_delay(config, item.attempts), config.queue.max_attempts
        )
        try:
            result_label = "dead-letter" if status == "dead_letter" else "временная ошибка, будет повтор"
            notifier.incident(
                f"processing:{item.computer_name}",
                f"Обработка события {item.computer_name}",
                f"Идентификатор: {item.event_id}\nКомпьютер: {item.computer_name}"
                f"\nРезультат: {result_label}\nОшибка: {str(exc)[:500]}",
            )
        except Exception:
            LOG.exception("processing_notification_failed event_id=%s", item.event_id)
        return status
    queue.finish(item, "processed", result)
    try:
        notifier.computer(payload, result)
        marker = ";owner_changed:"
        if marker in result and decision.username:
            transition = result.split(marker, 1)[1]
            previous = transition.rsplit("->", 1)[0]
            notifier.owner_change(
                payload,
                previous,
                decision.username,
                decision.confirmations,
                item.event_id,
            )
    except Exception:
        LOG.exception("report_notification_failed event_id=%s", item.event_id)
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
        result = process_item(config, queue, client, notifier, item) if item else None
        queue.purge(config.queue.retention_days)
        return result
    finally:
        if own_queue:
            client.close()
            queue.close()


def run_forever(config: GatewayConfig) -> None:
    queue, client = EventQueue(config.queue.path), SnipeITClient(config.snipeit)
    notifier = Notifier(config, queue)
    owner = f"{socket.gethostname()}:{os.getpid()}"
    next_purge = 0.0
    try:
        while True:
            try:
                item = queue.claim(owner, config.queue.lease_seconds)
                if item:
                    process_item(config, queue, client, notifier, item)
                else:
                    time.sleep(2)
                current = time.monotonic()
                if current >= next_purge:
                    purged = queue.purge(config.queue.retention_days)
                    if purged:
                        LOG.info("retention_purge removed=%s", purged)
                    next_purge = current + 3600
                notifier.recovered("queue", "durable queue")
            except sqlite3.Error as exc:
                LOG.exception("queue_error")
                try:
                    notifier.incident(
                        "queue",
                        "Очередь Gateway",
                        f"Ошибка надёжной очереди: {type(exc).__name__}",
                    )
                except (OSError, sqlite3.Error):
                    LOG.exception("queue_error_notification_failed")
                time.sleep(5)
    finally:
        client.close()
        queue.close()
