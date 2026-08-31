from __future__ import annotations

import datetime as dt
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .protocol import DecodedEvent


def now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


@dataclass(frozen=True)
class QueueItem:
    event_id: str
    computer_name: str
    event_type: str
    generation: int
    observed_at: str
    source: str
    payload: dict
    attempts: int


class EventQueue:
    TERMINAL = {"processed", "rejected", "dead_letter", "stale"}

    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def close(self) -> None:
        self.db.close()

    def _migrate(self) -> None:
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(
          event_id TEXT PRIMARY KEY, computer_name TEXT NOT NULL, event_type TEXT NOT NULL,
          generation INTEGER NOT NULL, observed_at TEXT NOT NULL, received_at TEXT NOT NULL,
          source TEXT NOT NULL CHECK(source IN ('https','smtp','migration')),
          key_id TEXT NOT NULL, payload_json TEXT NOT NULL, envelope_json TEXT NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('pending','processing','retry','processed','rejected','dead_letter','stale')),
          attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT NOT NULL,
          lease_owner TEXT, lease_until TEXT, result TEXT, last_error TEXT, updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS events_claim ON events(status,next_attempt_at,received_at);
        CREATE TABLE IF NOT EXISTS watermarks(
          computer_name TEXT PRIMARY KEY, generation INTEGER NOT NULL, observed_at TEXT NOT NULL,
          event_id TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS notification_state(
          notification_key TEXT PRIMARY KEY, first_failure_at TEXT, last_failure_at TEXT,
          last_sent_at TEXT, recovered_at TEXT, occurrences INTEGER NOT NULL DEFAULT 0
        );
        INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(1,datetime('now'));
        """)

    def enqueue(self, event: DecodedEvent, source: str) -> tuple[str, bool]:
        timestamp = now()
        cursor = self.db.execute(
            """INSERT OR IGNORE INTO events(event_id,computer_name,event_type,generation,observed_at,
               received_at,source,key_id,payload_json,envelope_json,status,next_attempt_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?, 'pending',?,?)""",
            (
                event.event_id,
                event.computer_name,
                event.event_type,
                event.generation,
                event.observed_at.isoformat(),
                timestamp,
                source,
                event.key_id,
                json.dumps(event.payload, separators=(",", ":")),
                json.dumps(event.envelope, separators=(",", ":")),
                timestamp,
                timestamp,
            ),
        )
        status = self.status(event.event_id) or "pending"
        return status, cursor.rowcount == 0

    def status(self, event_id: str) -> str | None:
        row = self.db.execute("SELECT status FROM events WHERE event_id=?", (event_id,)).fetchone()
        return row[0] if row else None

    def claim(self, owner: str, lease_seconds: int) -> QueueItem | None:
        current = dt.datetime.now(dt.UTC)
        lease_until = (current + dt.timedelta(seconds=lease_seconds)).isoformat()
        with self.db:
            row = self.db.execute(
                """SELECT * FROM events WHERE
                   ((status IN ('pending','retry') AND next_attempt_at<=?) OR
                    (status='processing' AND lease_until<?))
                   ORDER BY received_at,event_id LIMIT 1""",
                (current.isoformat(), current.isoformat()),
            ).fetchone()
            if not row:
                return None
            updated = self.db.execute(
                """UPDATE events SET status='processing',attempts=attempts+1,lease_owner=?,lease_until=?,updated_at=?
                   WHERE event_id=? AND status=?""",
                (owner, lease_until, current.isoformat(), row["event_id"], row["status"]),
            )
            if updated.rowcount != 1:
                return None
            row = self.db.execute(
                "SELECT * FROM events WHERE event_id=?", (row["event_id"],)
            ).fetchone()
        return QueueItem(
            row["event_id"],
            row["computer_name"],
            row["event_type"],
            row["generation"],
            row["observed_at"],
            row["source"],
            json.loads(row["payload_json"]),
            row["attempts"],
        )

    def stale_reason(self, item: QueueItem) -> str | None:
        row = self.db.execute(
            "SELECT generation,observed_at FROM watermarks WHERE computer_name=?",
            (item.computer_name,),
        ).fetchone()
        if not row:
            return None
        if item.generation < row["generation"]:
            return "generation older than applied watermark"
        if item.generation == row["generation"] and item.observed_at <= row["observed_at"]:
            return "observation not newer than applied watermark"
        return None

    def finish(self, item: QueueItem, status: str, result: str = "") -> None:
        if status not in self.TERMINAL:
            raise ValueError("terminal status required")
        timestamp = now()
        with self.db:
            self.db.execute(
                "UPDATE events SET status=?,result=?,lease_owner=NULL,lease_until=NULL,updated_at=? WHERE event_id=?",
                (status, result[:2000], timestamp, item.event_id),
            )
            if status == "processed":
                self.db.execute(
                    """INSERT INTO watermarks(computer_name,generation,observed_at,event_id,updated_at)
                       VALUES(?,?,?,?,?) ON CONFLICT(computer_name) DO UPDATE SET
                       generation=excluded.generation,observed_at=excluded.observed_at,
                       event_id=excluded.event_id,updated_at=excluded.updated_at""",
                    (
                        item.computer_name,
                        item.generation,
                        item.observed_at,
                        item.event_id,
                        timestamp,
                    ),
                )

    def retry(self, item: QueueItem, error: str, delay_seconds: int, max_attempts: int) -> str:
        terminal = item.attempts >= max_attempts
        status = "dead_letter" if terminal else "retry"
        next_at = (dt.datetime.now(dt.UTC) + dt.timedelta(seconds=delay_seconds)).isoformat()
        self.db.execute(
            """UPDATE events SET status=?,last_error=?,next_attempt_at=?,lease_owner=NULL,
               lease_until=NULL,updated_at=? WHERE event_id=?""",
            (status, error[:2000], next_at, now(), item.event_id),
        )
        return status

    def counts(self) -> dict[str, int]:
        return {
            row["status"]: row["n"]
            for row in self.db.execute("SELECT status,count(*) n FROM events GROUP BY status")
        }

    def purge(self, retention_days: int) -> int:
        cutoff = (dt.datetime.now(dt.UTC) - dt.timedelta(days=retention_days)).isoformat()
        cursor = self.db.execute(
            "DELETE FROM events WHERE status IN ('processed','rejected','stale') AND updated_at<?",
            (cutoff,),
        )
        return cursor.rowcount
