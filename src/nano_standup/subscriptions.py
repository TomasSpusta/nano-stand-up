"""Subscriptions: which guarantor email gets the brief for which instrument. Stored in SQLite.

A subscription starts as a request with a confirm link, and becomes active when the link is used.
It ends when its email is no longer the instrument's guarantor email, and a new confirm link starts it again.
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

# The schema version, kept in SQLite's `user_version` of the database file.
# `user_version` belongs to the whole file, which the record of sent briefs shares, but only this module sets it:
# it is the schema version of the subscription and confirm_link tables.
# Below 2: no table yet, or the first version's table, which allows only active subscriptions.
# 2: a subscription can end, and keeps the time it ended.
# 3: the confirm_link table, for requested subscriptions.
SCHEMA_VERSION = 3

# The tables of SCHEMA_VERSION, for a new database.
_SCHEMA = [
    # An ended subscription stays as a row, so the Monday job never checks or ends it twice.
    """
CREATE TABLE subscription (
    id INTEGER PRIMARY KEY,
    instrument_id TEXT NOT NULL,
    email TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'ended')),
    created_at TEXT NOT NULL,
    ended_at TEXT,
    UNIQUE (instrument_id, email)
)
""",
    # A requested subscription that waits for its confirm link. Only a hash of the token is kept, so a copy of the
    # database cannot be used to confirm anything.
    """
CREATE TABLE confirm_link (
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


class ConfirmLinkProblem(Enum):
    """Why a confirm link cannot be used now."""

    EXPIRED = "expired"
    USED = "used"
    UNKNOWN = "unknown"


class SubscriptionStore:
    def __init__(self, path: Path, confirm_link_lifetime: timedelta = CONFIRM_LINK_LIFETIME) -> None:
        self._path = path
        self._confirm_link_lifetime = confirm_link_lifetime
        with closing(self._connect()) as db, db:
            _create_or_upgrade_tables(db)

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

    def check(self, token: str, now: datetime) -> Subscription | ConfirmLinkProblem:
        """The subscription `confirm` would make active with the token now, or why it would not. Does not use it."""
        with closing(self._connect()) as db:
            return _confirm_link_subscription(db, token, now)

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

    def confirm(self, token: str, now: datetime) -> Subscription | ConfirmLinkProblem:
        """Make the requested subscription active and return it, or say why the link cannot be used.

        Each token works once, and only before it expires.
        """
        with closing(self._connect()) as db, db:
            subscription = _confirm_link_subscription(db, token, now)
            if isinstance(subscription, ConfirmLinkProblem):
                return subscription
            # "used_at IS NULL" makes a second confirm of the same token at the same moment find nothing to update.
            updated = db.execute(
                "UPDATE confirm_link SET used_at = ? WHERE token_hash = ? AND used_at IS NULL",
                (now.isoformat(), _hash(token)),
            )
            if updated.rowcount == 0:
                return ConfirmLinkProblem.USED
            _activate(db, subscription, now)
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


def _confirm_link_subscription(db: sqlite3.Connection, token: str, now: datetime) -> Subscription | ConfirmLinkProblem:
    """The subscription the token's link would make active now, or why the link cannot be used."""
    link = _confirm_link(db, token)
    if link is None:
        return ConfirmLinkProblem.UNKNOWN
    if link.used:
        return ConfirmLinkProblem.USED
    if now >= link.expires_at:
        return ConfirmLinkProblem.EXPIRED
    return Subscription(link.instrument_id, link.email)


def _activate(db: sqlite3.Connection, subscription: Subscription, now: datetime) -> None:
    """Store the subscription as active. One that is already there stays one, and one that ended starts again."""
    db.execute(
        "INSERT INTO subscription (instrument_id, email, status, created_at) VALUES (?, ?, 'active', ?)"
        " ON CONFLICT (instrument_id, email) DO UPDATE SET status = 'active', ended_at = NULL",
        (subscription.instrument_id, subscription.email, now.isoformat()),
    )


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _create_or_upgrade_tables(db: sqlite3.Connection) -> None:
    """Create the tables, or upgrade those an older version created, keeping their rows.

    Takes the write lock only when there is something to do.
    """
    if _version(db) >= SCHEMA_VERSION:
        return
    # One transaction: the upgrade happens fully or not at all, and two processes never upgrade at once.
    db.execute("BEGIN IMMEDIATE")
    # Read again under the lock: another process may have upgraded it meanwhile.
    version = _version(db)
    if version >= SCHEMA_VERSION:
        return
    table = db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'subscription'").fetchone()
    if table is None:
        for statement in _SCHEMA:
            db.execute(statement)
    else:
        if version < 2:
            _upgrade_to_2(db)
        _upgrade_to_3(db)
    db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _upgrade_to_2(db: sqlite3.Connection) -> None:
    """Allow the 'ended' status and add `ended_at`.

    SQLite cannot change a CHECK in place, so the rows are copied into a new table that replaces the old one.
    The statement is version 2's own, not _SCHEMA: a later version changes _SCHEMA and adds its own upgrade step.
    """
    db.execute(
        """
CREATE TABLE subscription_v2 (
    id INTEGER PRIMARY KEY,
    instrument_id TEXT NOT NULL,
    email TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'ended')),
    created_at TEXT NOT NULL,
    ended_at TEXT,
    UNIQUE (instrument_id, email)
)
"""
    )
    db.execute(
        "INSERT INTO subscription_v2 (id, instrument_id, email, status, created_at)"
        " SELECT id, instrument_id, email, status, created_at FROM subscription"
    )
    db.execute("DROP TABLE subscription")
    db.execute("ALTER TABLE subscription_v2 RENAME TO subscription")


def _upgrade_to_3(db: sqlite3.Connection) -> None:
    """Add the confirm_link table. The statement is version 3's own, as in _upgrade_to_2.

    IF NOT EXISTS: a database made before schema versions covered confirm links may have the table already, with
    these same columns, at version 0. It keeps its rows.
    """
    db.execute(
        """
CREATE TABLE IF NOT EXISTS confirm_link (
    token_hash TEXT PRIMARY KEY,
    instrument_id TEXT NOT NULL,
    email TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT
)
"""
    )


def _version(db: sqlite3.Connection) -> int:
    [version] = db.execute("PRAGMA user_version").fetchone()
    return int(version)
