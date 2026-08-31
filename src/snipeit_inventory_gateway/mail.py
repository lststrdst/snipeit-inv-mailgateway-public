from __future__ import annotations

import email
import imaplib
import logging
import time
from email.header import decode_header, make_header
from email.message import Message

from .config import GatewayConfig
from .errors import AuthenticationError, ValidationError
from .notifications import Notifier
from .protocol import decode_event
from .queue import EventQueue
from .snipeit import SnipeITClient
from .worker import process_item

LOG = logging.getLogger(__name__)
FOLDERS = {
    "weekly": "! Weekly Reports",
    "alert": "Alerts",
    "error": "Errors",
    "relay": "Offline Relay",
    "processed": "Processed Events",
    "rejected": "Rejected Events",
    "report": "Reports",
    "warning": "Warnings",
}


def decoded_subject(message: Message) -> str:
    try:
        return str(make_header(decode_header(message.get("Subject", "")))).strip()
    except (LookupError, UnicodeError):
        return ""


def sender_address(message: Message) -> str:
    return email.utils.parseaddr(message.get("From", ""))[1].lower()


def encrypted_attachment(message: Message) -> bytes:
    matches: list[bytes] = []
    for part in message.walk():
        filename = part.get_filename() or ""
        content_type = part.get_content_type().lower()
        if filename.lower().endswith(".snipeit-event.json") and content_type == "application/json":
            payload = part.get_payload(decode=True)
            if payload is not None:
                matches.append(payload)
    if len(matches) != 1:
        raise ValidationError("exactly one encrypted event attachment is required")
    return matches[0]


def classify(message: Message, allowed_from: list[str]) -> str | None:
    """Fixed priority: relay > weekly > error > warning > alert > report."""
    subject = decoded_subject(message)
    upper = subject.upper()
    sender_ok = sender_address(message) in {value.lower() for value in allowed_from}
    new_relay = (
        upper.startswith("[SNIPEIT-INVENTORY] RELAY:") and message.get("X-SnipeIT-Relay") == "1"
    )
    legacy_relay = upper.startswith("[SNIPEIT-RELAY]")
    if sender_ok and (new_relay or legacy_relay):
        try:
            encrypted_attachment(message)
            return "relay"
        except ValidationError:
            return "rejected"
    project = (
        upper.startswith("[SNIPEIT-INVENTORY]")
        or upper.startswith("[PCINV-")
        or upper.startswith("PC INVENTORY ")
    )
    if not project:
        return None
    if "WEEKLY" in upper:
        return "weekly"
    if "ERROR" in upper:
        return "error"
    if "WARNING" in upper or "WARN" in upper:
        return "warning"
    if "ALERT" in upper:
        return "alert"
    if "REPORT" in upper:
        return "report"
    return None


def folder_path(config: GatewayConfig, key: str) -> str:
    return f"{config.imap.parent_folder}/{FOLDERS[key]}"


def move_uid(client: imaplib.IMAP4_SSL, uid: bytes, destination: str) -> None:
    typ, _ = client.uid("COPY", uid, destination)
    if typ != "OK":
        raise imaplib.IMAP4.error("COPY failed")
    typ, _ = client.uid("STORE", uid, "+FLAGS.SILENT", r"(\Deleted)")
    if typ != "OK":
        raise imaplib.IMAP4.error("STORE failed")
    client.expunge()


def create_folders(client: imaplib.IMAP4_SSL, config: GatewayConfig) -> None:
    client.create(config.imap.parent_folder)
    for name in FOLDERS.values():
        client.create(
            folder_path(config, next(key for key, value in FOLDERS.items() if value == name))
        )


def ingest_message(
    config: GatewayConfig, queue: EventQueue, client: SnipeITClient, notifier: Notifier, raw: bytes
) -> str | None:
    if len(raw) > config.imap.max_message_bytes:
        return "rejected"
    message = email.message_from_bytes(raw)
    route = classify(message, config.imap.allowed_from)
    if route != "relay":
        return route
    try:
        event = decode_event(encrypted_attachment(message), config, enforce_freshness=False)
        status, duplicate = queue.enqueue(event, "smtp")
    except (AuthenticationError, ValidationError):
        return "rejected"
    if duplicate and status in {"processed", "stale"}:
        return "processed"
    if duplicate and status in {"rejected", "dead_letter"}:
        return "rejected"
    deadline = time.monotonic() + config.imap.terminal_wait_seconds
    worker_id = "imap-inline"
    while time.monotonic() < deadline:
        item = queue.claim(worker_id, config.queue.lease_seconds)
        if item is None:
            current = queue.status(event.event_id)
            if current in {"processed", "stale"}:
                return "processed"
            if current in {"rejected", "dead_letter"}:
                return "rejected"
            time.sleep(0.1)
            continue
        result = process_item(config, queue, client, notifier, item)
        if item.event_id == event.event_id:
            if result in {"processed", "stale"}:
                return "processed"
            if result in {"rejected", "dead_letter"}:
                return "rejected"
            return "relay"
    return "relay"


def collect(config: GatewayConfig, dry_run: bool = False) -> dict[str, int]:
    counts: dict[str, int] = {}
    queue = None if dry_run else EventQueue(config.queue.path)
    snipe = None if dry_run else SnipeITClient(config.snipeit)
    notifier = None if dry_run else Notifier(config, queue)
    scanned = 0
    try:
        with imaplib.IMAP4_SSL(config.imap.host, config.imap.port) as imap:
            imap.login(config.imap.user, config.imap.password.get_secret_value())
            create_folders(imap, config)
            source_folders = [config.imap.inbox, folder_path(config, "relay")]
            for source in source_folders:
                if scanned >= config.imap.max_messages_per_run:
                    break
                typ, _ = imap.select(source)
                if typ != "OK":
                    raise imaplib.IMAP4.error(f"cannot select project folder: {source}")
                typ, data = imap.uid("SEARCH", None, "ALL")
                if typ != "OK":
                    raise imaplib.IMAP4.error("search failed")
                uids = data[0].split() if data and data[0] else []
                for uid in uids[: config.imap.max_messages_per_run - scanned]:
                    scanned += 1
                    typ, fetched = imap.uid("FETCH", uid, "(RFC822)")
                    if typ != "OK" or not fetched or not isinstance(fetched[0], tuple):
                        continue
                    raw = fetched[0][1]
                    if dry_run:
                        route = classify(email.message_from_bytes(raw), config.imap.allowed_from)
                    else:
                        route = ingest_message(config, queue, snipe, notifier, raw)
                    if route:
                        counts[route] = counts.get(route, 0) + 1
                        destination = folder_path(config, route)
                        if not dry_run and destination != source:
                            move_uid(imap, uid, destination)
        if notifier:
            notifier.recovered("imap", "IMAP collector")
    except (imaplib.IMAP4.error, OSError) as exc:
        if notifier:
            notifier.incident(
                "imap", "[SNIPEIT-GATEWAY] IMAP ERROR", f"IMAP collector: {type(exc).__name__}"
            )
        raise
    finally:
        if snipe:
            snipe.close()
        if queue:
            queue.close()
    return counts
