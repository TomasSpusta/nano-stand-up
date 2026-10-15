"""Subscriptions: which guarantor email gets the brief for which instrument. Stored in SQLite.

A subscription starts as a request with a confirm link, and becomes active when the link is opened.
It ends when the guarantor uses the unsubscribe link in one of its briefs.
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
# Random bytes in a confirm or unsubscribe token. 32 bytes cannot be guessed.
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
    # One unsubscribe link per brief, for the subscription the brief went to. Only a hash of the token is kept.
    # A link does not expire, because a brief may be read weeks later. `used_at` is set on every link of the
    # subscription when it ends, so no old link can end a later subscription to the same instrument.
    """
    CREATE TABLE IF NOT EXISTS unsubscribe_link (
        token_hash TEXT PRIMARY KEY,
        instrument_id TEXT NOT NULL,
        email TEXT NOT NULL,
        created_at TEXT NOT NULL,
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


class UnsubscribeOutcome(Enum):
    UNSUBSCRIBED = "unsubscribed"
    # Only from `check_unsubscribe`: the link would end an active subscription.
    READY = "ready"
    # The link was used, or its subscription has ended in another way.
    ALREADY_UNSUBSCRIBED = "already unsubscribed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class UnsubscribeResult:
    outcome: UnsubscribeOutcome
    # The subscription that ended when the outcome is UNSUBSCRIBED, otherwise None.
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

    def check(self, token: str, now: datetime) -> ConfirmOutcome:
        """What `confirm` would do with the token now, without using it: READY, EXPIRED, USED, or UNKNOWN."""
        with closing(self._connect()) as db:
            row = _confirm_link_row(db, token)
        return _refusal(row, now) or ConfirmOutcome.READY

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
            row = _confirm_link_row(db, token)
            refusal = _refusal(row, now)
            if refusal or row is None:
                return ConfirmResult(refusal or ConfirmOutcome.UNKNOWN)
            instrument_id, email, _, _ = row
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

    def unsubscribe_token(self, subscription: Subscription, now: datetime) -> str:
        """A new token for the unsubscribe link in one brief of the subscription. It is not stored."""
        token = secrets.token_urlsafe(TOKEN_BYTES)
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO unsubscribe_link (token_hash, instrument_id, email, created_at) VALUES (?, ?, ?, ?)",
                (_hash(token), subscription.instrument_id, subscription.email, now.isoformat()),
            )
        return token

    def check_unsubscribe(self, token: str) -> UnsubscribeOutcome:
        """What `unsubscribe` would do with the token, without using it: READY, ALREADY_UNSUBSCRIBED, or UNKNOWN."""
        with closing(self._connect()) as db:
            found = _unsubscribe_link_subscription(db, token)
        return found if isinstance(found, UnsubscribeOutcome) else UnsubscribeOutcome.READY

    def unsubscribe(self, token: str, now: datetime) -> UnsubscribeResult:
        """End the subscription the token's link is for, and only that one."""
        with closing(self._connect()) as db, db:
            subscription = _unsubscribe_link_subscription(db, token)
            if isinstance(subscription, UnsubscribeOutcome):
                return UnsubscribeResult(subscription)
            if not _end(db, subscription, now):
                # Another request ended it at the same moment.
                return UnsubscribeResult(UnsubscribeOutcome.ALREADY_UNSUBSCRIBED)
        return UnsubscribeResult(UnsubscribeOutcome.UNSUBSCRIBED, subscription)

    def list_active(self) -> list[Subscription]:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT instrument_id, email FROM subscription WHERE status = 'active' ORDER BY id"
            ).fetchall()
        return [Subscription(instrument_id, email) for instrument_id, email in rows]


# A confirm_link row: instrument_id, email, expires_at, used_at.
_ConfirmLinkRow = tuple[str, str, str, str | None]


def _confirm_link_row(db: sqlite3.Connection, token: str) -> _ConfirmLinkRow | None:
    row: _ConfirmLinkRow | None = db.execute(
        "SELECT instrument_id, email, expires_at, used_at FROM confirm_link WHERE token_hash = ?", (_hash(token),)
    ).fetchone()
    return row


def _refusal(row: _ConfirmLinkRow | None, now: datetime) -> ConfirmOutcome | None:
    """Why the link cannot be used now, or None when it can."""
    if row is None:
        return ConfirmOutcome.UNKNOWN
    _, _, expires_at, used_at = row
    if used_at is not None:
        return ConfirmOutcome.USED
    if now >= datetime.fromisoformat(expires_at):
        return ConfirmOutcome.EXPIRED
    return None


def _activate(db: sqlite3.Connection, subscription: Subscription, now: datetime) -> None:
    """Store the subscription as active. One that is already there stays one."""
    db.execute(
        "INSERT INTO subscription (instrument_id, email, status, created_at) VALUES (?, ?, 'active', ?)"
        " ON CONFLICT (instrument_id, email) DO UPDATE SET status = 'active'",
        (subscription.instrument_id, subscription.email, now.isoformat()),
    )


def _unsubscribe_link_subscription(db: sqlite3.Connection, token: str) -> Subscription | UnsubscribeOutcome:
    """The active subscription the link would end, or why it cannot end one."""
    row: tuple[str, str, str | None] | None = db.execute(
        "SELECT instrument_id, email, used_at FROM unsubscribe_link WHERE token_hash = ?", (_hash(token),)
    ).fetchone()
    if row is None:
        return UnsubscribeOutcome.UNKNOWN
    instrument_id, email, used_at = row
    active = db.execute(
        "SELECT 1 FROM subscription WHERE instrument_id = ? AND email = ? AND status = 'active'",
        (instrument_id, email),
    ).fetchone()
    if used_at is not None or active is None:
        return UnsubscribeOutcome.ALREADY_UNSUBSCRIBED
    return Subscription(instrument_id, email)


def _end(db: sqlite3.Connection, subscription: Subscription, now: datetime) -> bool:
    """End an active subscription and use up all its unsubscribe links. False when it was not active.

    The subscription table allows only active rows for now, so ending one deletes its row.
    """
    ended = db.execute(
        "DELETE FROM subscription WHERE instrument_id = ? AND email = ? AND status = 'active'",
        (subscription.instrument_id, subscription.email),
    )
    db.execute(
        "UPDATE unsubscribe_link SET used_at = ? WHERE instrument_id = ? AND email = ? AND used_at IS NULL",
        (now.isoformat(), subscription.instrument_id, subscription.email),
    )
    return ended.rowcount == 1


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
