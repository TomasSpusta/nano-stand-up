"""Subscriptions: which guarantor email gets the brief for which instrument. Stored in SQLite.

A subscription starts as a request with a confirm link, and becomes active when the link is opened.
"""

import hashlib
import secrets
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

from nano_standup.domain import normalize_email

# How long a confirm link works after the request.
CONFIRM_LINK_LIFETIME = timedelta(hours=24)
# Random bytes in a confirm token. 32 bytes cannot be guessed.
TOKEN_BYTES = 32

# Each statement creates only what is missing, so a database file from an older version opens and gains the new
# tables without losing its subscriptions.
_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS subscription (
        id INTEGER PRIMARY KEY,
        instrument_id TEXT NOT NULL,
        email TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('active')),
        created_at TEXT NOT NULL,
        UNIQUE (instrument_id, email)
    )
    """,
    # A requested subscription that waits for its confirm link. Only a hash of the token is kept, so a copy of the
    # database cannot be used to confirm anything.
    """
    CREATE TABLE IF NOT EXISTS confirm_link (
        token_hash TEXT PRIMARY KEY,
        instrument_id TEXT NOT NULL,
        email TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        used_at TEXT
    )
    """,
]


@dataclass(frozen=True)
class Subscription:
    instrument_id: str
    email: str


@dataclass(frozen=True)
class PendingSubscription:
    """A requested subscription. The token goes in the confirm link and is not stored."""

    token: str
    expires_at: datetime


class ConfirmOutcome(Enum):
    CONFIRMED = "confirmed"
    EXPIRED = "expired"
    USED = "used"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ConfirmResult:
    outcome: ConfirmOutcome
    # The active subscription when the outcome is CONFIRMED, otherwise None.
    subscription: Subscription | None = None


class SubscriptionStore:
    def __init__(self, path: Path, confirm_link_lifetime: timedelta = CONFIRM_LINK_LIFETIME) -> None:
        self._path = path
        self._confirm_link_lifetime = confirm_link_lifetime
        with closing(self._connect()) as db, db:
            for statement in _SCHEMA:
                db.execute(statement)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    def add_active(self, instrument_id: str, email: str, now: datetime) -> Subscription:
        """Add an active subscription. Adding the same one again keeps one."""
        subscription = Subscription(instrument_id, normalize_email(email))
        with closing(self._connect()) as db, db:
            _activate(db, subscription, now)
        return subscription

    def request(self, instrument_id: str, email: str, now: datetime) -> PendingSubscription:
        """Request a subscription. It becomes active only when `confirm` gets the returned token in time."""
        token = secrets.token_urlsafe(TOKEN_BYTES)
        expires_at = now + self._confirm_link_lifetime
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO confirm_link (token_hash, instrument_id, email, created_at, expires_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (_hash(token), instrument_id, normalize_email(email), now.isoformat(), expires_at.isoformat()),
            )
        return PendingSubscription(token, expires_at)

    def confirm(self, token: str, now: datetime) -> ConfirmResult:
        """Make the requested subscription active. Each token works once, and only before it expires."""
        with closing(self._connect()) as db, db:
            row = db.execute(
                "SELECT instrument_id, email, expires_at, used_at FROM confirm_link WHERE token_hash = ?",
                (_hash(token),),
            ).fetchone()
            if row is None:
                return ConfirmResult(ConfirmOutcome.UNKNOWN)
            instrument_id, email, expires_at, used_at = row
            if used_at is not None:
                return ConfirmResult(ConfirmOutcome.USED)
            if now >= datetime.fromisoformat(expires_at):
                return ConfirmResult(ConfirmOutcome.EXPIRED)
            # "used_at IS NULL" makes a second confirm of the same token at the same moment find nothing to update.
            updated = db.execute(
                "UPDATE confirm_link SET used_at = ? WHERE token_hash = ? AND used_at IS NULL",
                (now.isoformat(), _hash(token)),
            )
            if updated.rowcount == 0:
                return ConfirmResult(ConfirmOutcome.USED)
            subscription = Subscription(instrument_id, email)
            _activate(db, subscription, now)
        return ConfirmResult(ConfirmOutcome.CONFIRMED, subscription)

    def list_active(self) -> list[Subscription]:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT instrument_id, email FROM subscription WHERE status = 'active' ORDER BY id"
            ).fetchall()
        return [Subscription(instrument_id, email) for instrument_id, email in rows]


def _activate(db: sqlite3.Connection, subscription: Subscription, now: datetime) -> None:
    """Store the subscription as active. One that is already there stays one."""
    db.execute(
        "INSERT INTO subscription (instrument_id, email, status, created_at) VALUES (?, ?, 'active', ?)"
        " ON CONFLICT (instrument_id, email) DO UPDATE SET status = 'active'",
        (subscription.instrument_id, subscription.email, now.isoformat()),
    )


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
