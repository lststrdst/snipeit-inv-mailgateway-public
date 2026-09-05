from __future__ import annotations

import datetime as dt
import email.message
import hashlib
import smtplib
import ssl

from .config import GatewayConfig
from .mail_contract import mail_subject, subject_matches_category
from .queue import EventQueue
from .reports import (
    build_inventory_rows,
    computer_report,
    owner_change_report,
    weekly_report,
)


class Notifier:
    def __init__(self, config: GatewayConfig, queue: EventQueue):
        self.config, self.queue = config, queue

    @staticmethod
    def _logo() -> bytes | None:
        # В публичной редакции нет production-логотипа: используется текстовая шапка.
        return None

    def _send(
        self,
        subject: str,
        body: str,
        html_body: str | None = None,
        category: str = "computer-report",
    ) -> None:
        if not subject_matches_category(subject, category):
            raise ValueError("notification subject does not match its routing category")
        message = email.message.EmailMessage()
        message["From"] = self.config.smtp.from_address
        message["To"] = self.config.smtp.to_address
        message["Subject"] = subject
        message["X-SnipeIT-Category"] = category
        message["X-SnipeIT-Mail-Class"] = "notification"
        message["X-SnipeIT-Gateway-Version"] = "1.0.0"
        message["Auto-Submitted"] = "auto-generated"
        message["X-Auto-Response-Suppress"] = "All"
        message.set_content(body)
        logo = self._logo()
        if html_body:
            message.add_alternative(html_body, subtype="html")
            if logo:
                message.get_payload()[-1].add_related(
                    logo,
                    maintype="image",
                    subtype="png",
                    cid="<inventory-logo>",
                    filename="logo.png",
                    disposition="inline",
                )
        with smtplib.SMTP(self.config.smtp.host, self.config.smtp.port, timeout=30) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(self.config.smtp.user, self.config.smtp.password.get_secret_value())
            smtp.send_message(message)

    def _send_once(
        self,
        key: str,
        fingerprint: str,
        subject: str,
        body: str,
        html_body: str,
        category: str,
    ) -> bool:
        if not self.queue.reserve_report(key, fingerprint, subject):
            return False
        try:
            self._send(subject, body, html_body, category)
        except Exception:
            self.queue.finish_report(key, fingerprint, False)
            raise
        self.queue.finish_report(key, fingerprint, True)
        return True

    def incident(self, key: str, subject: str, body: str) -> bool:
        row = self.queue.db.execute(
            "SELECT last_sent_at,occurrences FROM notification_state WHERE notification_key=?",
            (key,),
        ).fetchone()
        current = dt.datetime.now(dt.UTC)
        if row and row["last_sent_at"]:
            last = dt.datetime.fromisoformat(row["last_sent_at"])
            if (current - last).total_seconds() < self.config.notifications.throttle_seconds:
                self.queue.db.execute(
                    "UPDATE notification_state SET last_failure_at=?,occurrences=occurrences+1 WHERE notification_key=?",
                    (current.isoformat(), key),
                )
                return False
        self._send(
            mail_subject("error", f"{subject} · требуется внимание"), body, category="error"
        )
        self.queue.db.execute(
            """INSERT INTO notification_state(notification_key,first_failure_at,last_failure_at,last_sent_at,occurrences)
               VALUES(?,?,?,?,1) ON CONFLICT(notification_key) DO UPDATE SET
               last_failure_at=excluded.last_failure_at,last_sent_at=excluded.last_sent_at,
               occurrences=notification_state.occurrences+1,recovered_at=NULL""",
            (key, current.isoformat(), current.isoformat(), current.isoformat()),
        )
        return True

    def recovered(self, key: str, component: str) -> bool:
        row = self.queue.db.execute(
            "SELECT occurrences,recovered_at FROM notification_state WHERE notification_key=?",
            (key,),
        ).fetchone()
        if not row or row["recovered_at"]:
            return False
        current = dt.datetime.now(dt.UTC).isoformat()
        self._send(
            mail_subject("recovery", f"{component} · работа восстановлена"),
            f"Компонент снова работает. Ошибок до восстановления: {row['occurrences']}.",
            category="warning",
        )
        self.queue.db.execute(
            "UPDATE notification_state SET recovered_at=? WHERE notification_key=?", (current, key)
        )
        return True

    def computer(self, payload: dict, result: str) -> bool:
        subject, body, html_body, fingerprint = computer_report(
            payload, result, self._logo() is not None
        )
        computer = str(payload.get("computer_name") or "unknown").upper()
        return self._send_once(
            f"computer:{computer}", fingerprint, subject, body, html_body, "computer-report"
        )

    def owner_change(
        self,
        payload: dict,
        previous: str,
        current: str,
        confirmations: int,
        event_id: str,
    ) -> bool:
        subject, body, html_body = owner_change_report(
            payload, previous, current, confirmations, self._logo() is not None
        )
        fingerprint = hashlib.sha256(event_id.encode()).hexdigest()
        return self._send_once(
            f"owner-change:{event_id}", fingerprint, subject, body, html_body, "owner-change"
        )

    def weekly_inventory(self, client, generated_at: dt.datetime | None = None) -> bool:
        generated_at = generated_at or dt.datetime.now(dt.UTC)
        rows = build_inventory_rows(client.list_assets(), self.config, generated_at)
        subject, body, html_body = weekly_report(
            rows, self.config, generated_at, self._logo() is not None
        )
        iso = generated_at.isocalendar()
        period = f"{iso.year}-W{iso.week:02d}"
        fingerprint = hashlib.sha256(period.encode()).hexdigest()
        return self._send_once(
            f"weekly:{period}", fingerprint, subject, body, html_body, "weekly-report"
        )
