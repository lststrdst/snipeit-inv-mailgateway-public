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


@dataclass(frozen=True)
class OwnerDecision:
    username: str | None
    state: str
    confirmations: int = 0
    first_seen_at: str | None = None


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
        CREATE TABLE IF NOT EXISTS owner_candidates(
          computer_name TEXT PRIMARY KEY, confirmed_username TEXT,
          candidate_username TEXT, first_seen_at TEXT, last_seen_at TEXT,
          confirmations INTEGER NOT NULL DEFAULT 0, last_event_id TEXT,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS report_delivery(
          dedupe_key TEXT PRIMARY KEY, content_hash TEXT NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('reserved','sent')),
          subject TEXT NOT NULL, reserved_at TEXT NOT NULL, sent_at TEXT
        );
        INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(1,datetime('now'));
        INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(2,datetime('now'));
        """)

    def decide_owner(
        self,
        computer_name: str,
        proposed_username: str | None,
        observed_at: str,
        event_id: str,
        required_events: int,
        required_hours: int,
        window_days: int,
    ) -> OwnerDecision:
        """Confirm a new owner only after distinct, stable observations.

        An empty or invalid proposal is deliberately a no-op: it can never check an
        asset in or erase the last confirmed owner.
        """
        if not proposed_username:
            return OwnerDecision(None, "preserve_missing")
        username = proposed_username.lower()
        observed = dt.datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=dt.UTC)
        observed = observed.astimezone(dt.UTC)
        timestamp = now()
        with self.db:
            row = self.db.execute(
                "SELECT * FROM owner_candidates WHERE computer_name=?", (computer_name,)
            ).fetchone()
            if row and row["confirmed_username"] == username:
                self.db.execute(
                    """UPDATE owner_candidates SET candidate_username=?,first_seen_at=?,last_seen_at=?,
                       confirmations=1,last_event_id=?,updated_at=? WHERE computer_name=?""",
                    (
                        username,
                        observed.isoformat(),
                        observed.isoformat(),
                        event_id,
                        timestamp,
                        computer_name,
                    ),
                )
                return OwnerDecision(username, "confirmed_existing", 1, observed.isoformat())
            reset = not row or row["candidate_username"] != username
            if row and row["first_seen_at"]:
                first_existing = dt.datetime.fromisoformat(row["first_seen_at"])
                reset = reset or observed - first_existing > dt.timedelta(days=window_days)
            if reset:
                confirmed = row["confirmed_username"] if row else None
                self.db.execute(
                    """INSERT INTO owner_candidates(computer_name,confirmed_username,candidate_username,
                       first_seen_at,last_seen_at,confirmations,last_event_id,updated_at)
                       VALUES(?,?,?,?,?,1,?,?) ON CONFLICT(computer_name) DO UPDATE SET
                       candidate_username=excluded.candidate_username,first_seen_at=excluded.first_seen_at,
                       last_seen_at=excluded.last_seen_at,confirmations=1,last_event_id=excluded.last_event_id,
                       updated_at=excluded.updated_at""",
                    (
                        computer_name,
                        confirmed,
                        username,
                        observed.isoformat(),
                        observed.isoformat(),
                        event_id,
                        timestamp,
                    ),
                )
                return OwnerDecision(None, "pending", 1, observed.isoformat())
            confirmations = int(row["confirmations"])
            if row["last_event_id"] != event_id:
                confirmations += 1
            first_seen = dt.datetime.fromisoformat(row["first_seen_at"])
            self.db.execute(
                """UPDATE owner_candidates SET last_seen_at=?,confirmations=?,last_event_id=?,updated_at=?
                   WHERE computer_name=?""",
                (observed.isoformat(), confirmations, event_id, timestamp, computer_name),
            )
            old_enough = observed - first_seen >= dt.timedelta(hours=required_hours)
            if confirmations >= required_events and old_enough:
                self.db.execute(
                    "UPDATE owner_candidates SET confirmed_username=?,updated_at=? WHERE computer_name=?",
                    (username, timestamp, computer_name),
                )
                return OwnerDecision(username, "confirmed_new", confirmations, first_seen.isoformat())
            return OwnerDecision(None, "pending", confirmations, first_seen.isoformat())

    def reserve_report(self, dedupe_key: str, content_hash: str, subject: str) -> bool:
        with self.db:
            row = self.db.execute(
                "SELECT content_hash,status,reserved_at FROM report_delivery WHERE dedupe_key=?",
                (dedupe_key,),
            ).fetchone()
            if row and row["content_hash"] == content_hash:
                if row["status"] == "sent":
                    return False
                reserved = dt.datetime.fromisoformat(row["reserved_at"])
                if dt.datetime.now(dt.UTC) - reserved < dt.timedelta(minutes=15):
                    return False
            self.db.execute(
                """INSERT INTO report_delivery(dedupe_key,content_hash,status,subject,reserved_at,sent_at)
                   VALUES(?,?,'reserved',?,?,NULL) ON CONFLICT(dedupe_key) DO UPDATE SET
                   content_hash=excluded.content_hash,status='reserved',subject=excluded.subject,
                   reserved_at=excluded.reserved_at,sent_at=NULL""",
                (dedupe_key, content_hash, subject, now()),
            )
        return True

    def finish_report(self, dedupe_key: str, content_hash: str, sent: bool) -> None:
        if sent:
            self.db.execute(
                "UPDATE report_delivery SET status='sent',sent_at=? WHERE dedupe_key=? AND content_hash=?",
                (now(), dedupe_key, content_hash),
            )
        else:
            self.db.execute(
                "DELETE FROM report_delivery WHERE dedupe_key=? AND content_hash=? AND status='reserved'",
                (dedupe_key, content_hash),
            )

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
