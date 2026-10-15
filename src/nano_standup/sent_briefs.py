"""Record of sent briefs: one row per subscription and week, so a re-run of the Monday job sends no duplicates.

Stored in the same SQLite file as the subscriptions, in its own table.
"""

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from nano_standup.domain import Week
from nano_standup.subscriptions import Subscription

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sent_brief (
    instrument_id TEXT NOT NULL,
    email TEXT NOT NULL,
    week_monday TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    PRIMARY KEY (instrument_id, email, week_monday)
)
"""


class SentBriefStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        with closing(self._connect()) as db, db:
            db.execute(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    def record(self, subscription: Subscription, week: Week, sent_at: datetime) -> None:
        """Record that the subscription's brief for the week went out. Recording it again keeps the first time."""
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO sent_brief (instrument_id, email, week_monday, sent_at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT DO NOTHING",
                (subscription.instrument_id, subscription.email, week.monday.isoformat(), sent_at.isoformat()),
            )

    def was_sent(self, subscription: Subscription, week: Week) -> bool:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT 1 FROM sent_brief WHERE instrument_id = ? AND email = ? AND week_monday = ?",
                (subscription.instrument_id, subscription.email, week.monday.isoformat()),
            ).fetchone()
        return row is not None
