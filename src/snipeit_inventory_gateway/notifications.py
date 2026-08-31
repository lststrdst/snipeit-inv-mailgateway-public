from __future__ import annotations

import datetime as dt
import email.message
import smtplib
import ssl

from .config import GatewayConfig
from .queue import EventQueue


class Notifier:
    def __init__(self, config: GatewayConfig, queue: EventQueue):
        self.config, self.queue = config, queue

    def _send(self, subject: str, body: str) -> None:
        message = email.message.EmailMessage()
        message["From"] = self.config.smtp.from_address
        message["To"] = self.config.smtp.to_address
        message["Subject"] = subject
        message.set_content(body)
        with smtplib.SMTP(self.config.smtp.host, self.config.smtp.port, timeout=30) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(self.config.smtp.user, self.config.smtp.password.get_secret_value())
            smtp.send_message(message)

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
        self._send(subject, body)
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
            f"[SNIPEIT-GATEWAY] RECOVERED: {component}",
            f"Компонент восстановился. Предыдущих ошибок: {row['occurrences']}.",
        )
        self.queue.db.execute(
            "UPDATE notification_state SET recovered_at=? WHERE notification_key=?", (current, key)
        )
        return True

    def weekly_health(self) -> None:
        self._send(
            "[SNIPEIT-GATEWAY] WEEKLY HEALTH",
            "SnipeIT Inventory Gateway v1.0.0\n\nQueue: " + str(self.queue.counts()),
        )
