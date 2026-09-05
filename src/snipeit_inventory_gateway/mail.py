from __future__ import annotations

import base64
import email
import imaplib
import logging
import time
from email.header import decode_header, make_header
from email.message import Message

from .config import GatewayConfig
from .errors import AuthenticationError, ValidationError
from .mail_contract import PRODUCT, subject_matches_category
from .notifications import Notifier
from .protocol import decode_event
from .queue import EventQueue
from .snipeit import SnipeITClient
from .worker import process_item

LOG = logging.getLogger(__name__)
FOLDERS = {
    "report": "Reports",
    "weekly": "Weekly Reports",
    "owner_change": "Reports",
    "warning": "Errors",
    "alert": "Errors",
    "error": "Errors",
    "relay": "Errors",
    "processed": "Reports",
    "rejected": "Errors",
}
LEGACY_REPORT_FOLDERS = (
    "SnipeIT Inventory/! Weekly Reports",
    "SnipeIT Inventory/Alerts",
    "SnipeIT Inventory/Errors",
    "SnipeIT Inventory/Reports",
    "SnipeIT Inventory/Warnings",
    "SnipeIT Inventory/Processed Events",
    "SnipeIT Inventory/Rejected Events",
)
LEGACY_RELAY_FOLDER = "SnipeIT Inventory/Offline Relay"


def imap_mailbox(value: str) -> str:
    """Encode Unicode mailbox names using IMAP modified UTF-7."""
    output: list[str] = []
    pending: list[str] = []

    def flush() -> None:
        if not pending:
            return
        raw = "".join(pending).encode("utf-16-be")
        output.append("&" + base64.b64encode(raw).decode().rstrip("=").replace("/", ",") + "-")
        pending.clear()

    for character in value:
        if " " <= character <= "~":
            flush()
            output.append("&-" if character == "&" else character)
        else:
            pending.append(character)
    flush()
    encoded = "".join(output)
    return '"' + encoded.replace("\\", "\\\\").replace('"', '\\"') + '"'


def decoded_subject(message: Message) -> str:
    try:
        return str(make_header(decode_header(message.get("Subject", "")))).strip()
    except (LookupError, UnicodeError):
        return ""


def sender_address(message: Message) -> str:
    return email.utils.parseaddr(message.get("From", ""))[1].lower()


def recipient_addresses(message: Message) -> list[str]:
    values = [*(message.get_all("To", []) or []), *(message.get_all("Cc", []) or [])]
    return [address.lower() for _, address in email.utils.getaddresses(values) if address]


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


def classify(message: Message, allowed_from: list[str], expected_to: str) -> str | None:
    """Classify only authenticated project mail; unrelated INBOX mail stays untouched."""
    subject = decoded_subject(message)
    upper = subject.upper()
    sender_ok = sender_address(message) in {value.lower() for value in allowed_from}
    recipients = recipient_addresses(message)
    recipient_ok = len(recipients) == 1 and recipients[0] == expected_to.lower()
    route_ok = sender_ok and recipient_ok
    new_relay = (
        subject_matches_category(subject, "relay")
        and message.get("X-SnipeIT-Relay") == "1"
        and str(message.get("X-SnipeIT-Mail-Class") or "").lower() == "transport"
    )
    compatible_relay = (
        (
            upper.startswith("[SNIPEIT-INVENTORY] RELAY:")
            or upper.startswith("[SNIPEIT INVENTORY GATEWAY] РЕЗЕРВНАЯ ДОСТАВКА")
        )
        and message.get("X-SnipeIT-Relay") == "1"
    )
    legacy_relay = upper.startswith("[SNIPEIT-RELAY]")
    if route_ok and (new_relay or compatible_relay or legacy_relay):
        try:
            encrypted_attachment(message)
            return "relay"
        except ValidationError:
            return "rejected"
    category = str(message.get("X-SnipeIT-Category") or "").strip().lower()
    category_routes = {
        "computer-report": "report",
        "weekly-report": "weekly",
        "owner-change": "owner_change",
        "warning": "warning",
        "error": "error",
        "alert": "alert",
    }
    compatible_project_subject = upper.startswith(f"[{PRODUCT}]".upper())
    if route_ok and category in category_routes:
        if subject_matches_category(subject, category) or compatible_project_subject:
            return category_routes[category]
        return None
    if upper.startswith("[PCINV-") or upper.startswith("PC INVENTORY "):
        return "legacy" if route_ok else None
    project = (
        upper.startswith("[SNIPEIT-INVENTORY]")
        or upper.startswith("[SNIPEIT INVENTORY GATEWAY]")
    )
    if not project or not route_ok:
        return None
    if "WEEKLY" in upper:
        return "weekly"
    if "ERROR" in upper:
        return "error"
    if "WARNING" in upper or "WARN" in upper:
        return "warning"
    if "ALERT" in upper:
        return "alert"
    if "СМЕНА ПОЛЬЗОВАТЕЛЯ" in upper:
        return "owner_change"
    if "ОТЧЁТ ПО КОМПЬЮТЕРУ" in upper or "REPORT" in upper:
        return "report"
    return None


def folder_path(config: GatewayConfig, key: str) -> str:
    separator = config.imap.folder_separator
    relative = FOLDERS[key].replace("/", separator)
    return f"{config.imap.parent_folder}{separator}{relative}"


def move_uid(client: imaplib.IMAP4_SSL, uid: bytes, destination: str) -> None:
    typ, _ = client.uid("COPY", uid, imap_mailbox(destination))
    if typ != "OK":
        raise imaplib.IMAP4.error("COPY failed")
    typ, _ = client.uid("STORE", uid, "+FLAGS.SILENT", r"(\Deleted)")
    if typ != "OK":
        raise imaplib.IMAP4.error("STORE failed")
    client.expunge()


def create_folders(client: imaplib.IMAP4_SSL, config: GatewayConfig) -> None:
    separator = config.imap.folder_separator
    names = {config.imap.parent_folder}
    for relative in set(FOLDERS.values()):
        current = config.imap.parent_folder
        for part in relative.split("/"):
            current = f"{current}{separator}{part}"
            names.add(current)
    for name in sorted(names, key=lambda item: (item.count(separator), item)):
        client.create(imap_mailbox(name))


def ingest_message(
    config: GatewayConfig, queue: EventQueue, client: SnipeITClient, notifier: Notifier, raw: bytes
) -> str | None:
    if len(raw) > config.imap.max_message_bytes:
        return "rejected"
    message = email.message_from_bytes(raw)
    route = classify(message, config.imap.allowed_from, config.imap.user)
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
            return "error"
    return "error"


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
            source_folders = [
                (config.imap.inbox, False, False),
                (LEGACY_RELAY_FOLDER, False, True),
                *((source, True, True) for source in LEGACY_REPORT_FOLDERS),
            ]
            for source, archive_all, optional in source_folders:
                if scanned >= config.imap.max_messages_per_run:
                    break
                typ, _ = imap.select(imap_mailbox(source))
                if typ != "OK":
                    if optional:
                        continue
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
                        route = classify(
                            email.message_from_bytes(raw),
                            config.imap.allowed_from,
                            config.imap.user,
                        )
                    else:
                        route = ingest_message(config, queue, snipe, notifier, raw)
                    if archive_all:
                        route = "legacy"
                    if route:
                        counts[route] = counts.get(route, 0) + 1
                        destination = (
                            config.imap.trash_folder if route == "legacy" else folder_path(config, route)
                        )
                        if not dry_run and destination != source:
                            move_uid(imap, uid, destination)
        if notifier:
            notifier.recovered("imap", "сборщик резервной почты")
    except (imaplib.IMAP4.error, OSError) as exc:
        if notifier:
            notifier.incident(
                "imap", "Сборщик резервной почты", f"Ошибка IMAP: {type(exc).__name__}"
            )
        raise
    finally:
        if snipe:
            snipe.close()
        if queue:
            queue.close()
    return counts
