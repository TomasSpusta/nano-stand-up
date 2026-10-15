"""Subscriptions: which guarantor email gets the brief for which instrument. Stored in SQLite."""

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from nano_standup.domain import normalize_email


def _table_schema(name: str) -> str:
    """The subscription table as this version wants it. A new column must allow NULL or have a default,
    so the rows of an older table fit when it is upgraded.

    An ended subscription stays as a row, so the Monday job never checks or ends it twice.
    """
    # The name is in quotes because SQLite stores a renamed table that way, so a new and an upgraded table match.
    return f"""
CREATE TABLE "{name}" (
    id INTEGER PRIMARY KEY,
    instrument_id TEXT NOT NULL,
    email TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'ended')),
    created_at TEXT NOT NULL,
    ended_at TEXT,
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
            _create_or_upgrade_table(db)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    def add_active(self, instrument_id: str, email: str, now: datetime) -> Subscription:
        """Add an active subscription. Adding the same one again keeps one."""
        subscription = Subscription(instrument_id, normalize_email(email))
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO subscription (instrument_id, email, status, created_at) VALUES (?, ?, 'active', ?)"
                " ON CONFLICT (instrument_id, email) DO UPDATE SET status = 'active', ended_at = NULL",
                (subscription.instrument_id, subscription.email, now.isoformat()),
            )
        return subscription

    def list_active(self) -> list[Subscription]:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT instrument_id, email FROM subscription WHERE status = 'active' ORDER BY id"
            ).fetchall()
        return [Subscription(instrument_id, email) for instrument_id, email in rows]

    def end_because_guarantor_changed(self, subscription: Subscription, now: datetime) -> bool:
        """End an active subscription: its email is no longer the instrument's guarantor email.

        False when it was not active, for example because it had already ended.
        """
        with closing(self._connect()) as db, db:
            cursor = db.execute(
                "UPDATE subscription SET status = 'ended', ended_at = ?"
                " WHERE instrument_id = ? AND email = ? AND status = 'active'",
                (now.isoformat(), subscription.instrument_id, subscription.email),
            )
        return cursor.rowcount == 1


def _create_or_upgrade_table(db: sqlite3.Connection) -> None:
    """Create the subscription table, or upgrade one an older version created, keeping its rows.

    SQLite cannot change a column or a CHECK in place, so an older table is copied into a new one and replaced.
    """
    # One transaction: the upgrade happens fully or not at all, and two processes never upgrade at once.
    db.execute("BEGIN IMMEDIATE")
    row = db.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'subscription'").fetchone()
    if row is None:
        db.execute(_table_schema("subscription"))
        return
    if _same_sql(row[0], _table_schema("subscription")):
        return
    db.execute(_table_schema("subscription_upgrade"))
    new_columns = {column for _, column, *_ in db.execute("PRAGMA table_info(subscription_upgrade)")}
    kept = ", ".join(column for _, column, *_ in db.execute("PRAGMA table_info(subscription)") if column in new_columns)
    db.execute(f"INSERT INTO subscription_upgrade ({kept}) SELECT {kept} FROM subscription")
    db.execute("DROP TABLE subscription")
    db.execute("ALTER TABLE subscription_upgrade RENAME TO subscription")


def _same_sql(stored: str, wanted: str) -> bool:
    """SQLite keeps the CREATE statement as it was written, so compare it with whitespace collapsed."""
    return stored.split() == wanted.split()
