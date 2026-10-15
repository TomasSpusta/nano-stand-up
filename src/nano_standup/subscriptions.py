"""Subscriptions: which guarantor email gets the brief for which instrument. Stored in SQLite."""

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from nano_standup.domain import normalize_email

# The schema version, kept in SQLite's `user_version` of the database file.
# Below 2: no table yet, or the first version's table, which allows only active subscriptions.
# 2: a subscription can end, and keeps the time it ended.
SCHEMA_VERSION = 2


def _table_schema(name: str) -> str:
    """The subscription table of SCHEMA_VERSION.

    An ended subscription stays as a row, so the Monday job never checks or ends it twice.
    """
    return f"""
CREATE TABLE {name} (
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

    Takes the write lock only when there is something to do.
    """
    if _version(db) >= SCHEMA_VERSION:
        return
    # One transaction: the upgrade happens fully or not at all, and two processes never upgrade at once.
    db.execute("BEGIN IMMEDIATE")
    # Read again under the lock: another process may have upgraded it meanwhile.
    if _version(db) >= SCHEMA_VERSION:
        return
    table = db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'subscription'").fetchone()
    if table is None:
        db.execute(_table_schema("subscription"))
    else:
        _upgrade_to_2(db)
    db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _upgrade_to_2(db: sqlite3.Connection) -> None:
    """Allow the 'ended' status and add `ended_at`.

    SQLite cannot change a CHECK in place, so the rows are copied into a new table that replaces the old one.
    """
    db.execute(_table_schema("subscription_v2"))
    db.execute(
        "INSERT INTO subscription_v2 (id, instrument_id, email, status, created_at)"
        " SELECT id, instrument_id, email, status, created_at FROM subscription"
    )
    db.execute("DROP TABLE subscription")
    db.execute("ALTER TABLE subscription_v2 RENAME TO subscription")


def _version(db: sqlite3.Connection) -> int:
    [version] = db.execute("PRAGMA user_version").fetchone()
    return int(version)
