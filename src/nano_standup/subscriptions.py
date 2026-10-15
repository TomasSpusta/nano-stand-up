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
class RequestedSubscription:
    """A requested subscription. The token goes in the confirm link and is not stored."""

    token: str
    expires_at: datetime


class ConfirmOutcome(Enum):
    CONFIRMED = "confirmed"
    # Only from `check`: the link is valid and not used yet.
    READY = "ready"
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

    @property
    def confirm_link_lifetime(self) -> timedelta:
        return self._confirm_link_lifetime

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    def add_active(self, instrument_id: str, email: str, now: datetime) -> Subscription:
        """Add an active subscription. Adding the same one again keeps one."""
        subscription = Subscription(instrument_id, normalize_email(email))
        with closing(self._connect()) as db, db:
            _activate(db, subscription, now)
        return subscription

    def request(self, instrument_id: str, email: str, now: datetime) -> RequestedSubscription:
        """Request a subscription. It becomes active only when `confirm` gets the returned token in time."""
        token = secrets.token_urlsafe(TOKEN_BYTES)
        expires_at = now + self._confirm_link_lifetime
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO confirm_link (token_hash, instrument_id, email, created_at, expires_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (_hash(token), instrument_id, normalize_email(email), now.isoformat(), expires_at.isoformat()),
            )
        return RequestedSubscription(token, expires_at)

    def check(self, token: str, now: datetime) -> ConfirmOutcome:
        """What `confirm` would do with the token now, without using it: READY, EXPIRED, USED, or UNKNOWN."""
        with closing(self._connect()) as db:
            link = _confirm_link(db, token)
        return _refusal(link, now) or ConfirmOutcome.READY

    def has_recent_request(self, instrument_id: str, since: datetime, now: datetime) -> bool:
        """True when a request for the instrument made after `since` still has a link that works."""
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT created_at, expires_at FROM confirm_link WHERE instrument_id = ? AND used_at IS NULL",
                (instrument_id,),
            ).fetchall()
        return any(
            datetime.fromisoformat(created_at) > since and now < datetime.fromisoformat(expires_at)
            for created_at, expires_at in rows
        )

    def confirm(self, token: str, now: datetime) -> ConfirmResult:
        """Make the requested subscription active. Each token works once, and only before it expires."""
        with closing(self._connect()) as db, db:
            link = _confirm_link(db, token)
            refusal = _refusal(link, now)
            if refusal or link is None:
                return ConfirmResult(refusal or ConfirmOutcome.UNKNOWN)
            # "used_at IS NULL" makes a second confirm of the same token at the same moment find nothing to update.
            updated = db.execute(
                "UPDATE confirm_link SET used_at = ? WHERE token_hash = ? AND used_at IS NULL",
                (now.isoformat(), _hash(token)),
            )
            if updated.rowcount == 0:
                return ConfirmResult(ConfirmOutcome.USED)
            subscription = Subscription(link.instrument_id, link.email)
            _activate(db, subscription, now)
        return ConfirmResult(ConfirmOutcome.CONFIRMED, subscription)

    def list_active(self) -> list[Subscription]:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT instrument_id, email FROM subscription WHERE status = 'active' ORDER BY id"
            ).fetchall()
        return [Subscription(instrument_id, email) for instrument_id, email in rows]


@dataclass(frozen=True)
class _ConfirmLink:
    """A row of the confirm_link table."""

    instrument_id: str
    email: str
    expires_at: datetime
    used: bool


def _confirm_link(db: sqlite3.Connection, token: str) -> _ConfirmLink | None:
    row: tuple[str, str, str, str | None] | None = db.execute(
        "SELECT instrument_id, email, expires_at, used_at FROM confirm_link WHERE token_hash = ?", (_hash(token),)
    ).fetchone()
    if row is None:
        return None
    instrument_id, email, expires_at, used_at = row
    return _ConfirmLink(instrument_id, email, datetime.fromisoformat(expires_at), used=used_at is not None)


def _refusal(link: _ConfirmLink | None, now: datetime) -> ConfirmOutcome | None:
    """Why the link cannot be used now, or None when it can."""
    if link is None:
        return ConfirmOutcome.UNKNOWN
    if link.used:
        return ConfirmOutcome.USED
    if now >= link.expires_at:
        return ConfirmOutcome.EXPIRED
    return None


def _activate(db: sqlite3.Connection, subscription: Subscription, now: datetime) -> None:
    """Store the subscription as active. One that is already there stays one."""
    db.execute(
        "INSERT INTO subscription (instrument_id, email, status, created_at) VALUES (?, ?, 'active', ?)"
        " ON CONFLICT (instrument_id, email) DO UPDATE SET status = 'active'",
        (subscription.instrument_id, subscription.email, now.isoformat()),
    )


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
