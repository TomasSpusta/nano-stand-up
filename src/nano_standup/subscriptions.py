"""Subscriptions: which guarantor email gets the brief for which instrument. Stored in SQLite."""

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from nano_standup.domain import normalize_email

_SCHEMA = """
CREATE TABLE IF NOT EXISTS subscription (
    id INTEGER PRIMARY KEY,
    instrument_id TEXT NOT NULL,
    email TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active')),
    created_at TEXT NOT NULL,
    UNIQUE (instrument_id, email)
)
"""


@dataclass(frozen=True)
class Subscription:
    instrument_id: str
    email: str


class SubscriptionStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        with closing(self._connect()) as db, db:
            db.execute(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    def add_active(self, instrument_id: str, email: str, now: datetime) -> Subscription:
        """Add an active subscription. Adding the same one again keeps one."""
        subscription = Subscription(instrument_id, normalize_email(email))
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO subscription (instrument_id, email, status, created_at) VALUES (?, ?, 'active', ?)"
                " ON CONFLICT (instrument_id, email) DO UPDATE SET status = 'active'",
                (subscription.instrument_id, subscription.email, now.isoformat()),
            )
        return subscription

    def list_active(self) -> list[Subscription]:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT instrument_id, email FROM subscription WHERE status = 'active' ORDER BY id"
            ).fetchall()
        return [Subscription(instrument_id, email) for instrument_id, email in rows]
