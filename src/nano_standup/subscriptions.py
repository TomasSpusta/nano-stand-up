"""Subscriptions: which guarantor email gets the brief for which instrument. Stored in SQLite.

A subscription starts as a request with a confirm link, and becomes active when the link is used.
It ends when its email is no longer the instrument's guarantor email, or when the guarantor uses the unsubscribe link
in one of its briefs. A new confirm link starts it again.
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

# The schema version, kept in SQLite's `user_version` of the database file.
# `user_version` belongs to the whole file, which the record of sent briefs shares, but only this module sets it:
# it is the schema version of the subscription, confirm_link, and unsubscribe_link tables.
# Below 2: no table yet, or the first version's table, which allows only active subscriptions.
# 2: a subscription can end, and keeps the time it ended.
# 3: the confirm_link table, for requested subscriptions.
# 4: the unsubscribe_link table, for the unsubscribe links in the briefs.
SCHEMA_VERSION = 4

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
    # One unsubscribe link per brief, for the subscription the brief went to. Only a hash of the token is kept.
    # A link does not expire, because a brief may be read weeks later. When the subscription ends, in any way,
    # `used_at` and `end_reason` are set on all its links, so no old link can end a later subscription to the same
    # instrument. Used rows are kept, so an old link still says why the subscription ended. The table grows by one
    # row per brief, a few thousand rows a year, which SQLite handles with ease.
    """
CREATE TABLE unsubscribe_link (
    token_hash TEXT PRIMARY KEY,
    instrument_id TEXT NOT NULL,
    email TEXT NOT NULL,
    created_at TEXT NOT NULL,
    used_at TEXT,
    end_reason TEXT
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


class UnsubscribeLinkProblem(Enum):
    """Why an unsubscribe link cannot end a subscription now."""

    # The link was used, or its subscription was ended by another of its unsubscribe links.
    ALREADY_UNSUBSCRIBED = "already unsubscribed"
    # The subscription ended because the booking system no longer lists its email as the instrument's guarantor.
    GUARANTOR_CHANGED = "guarantor changed"
    UNKNOWN = "unknown"


class _EndReason(Enum):
    """Why a subscription ended. Kept on its unsubscribe links, as `end_reason`."""

    UNSUBSCRIBED = "unsubscribed"
    GUARANTOR_CHANGED = "guarantor changed"


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

    def unsubscribe_token(self, subscription: Subscription, now: datetime) -> str | None:
        """A new token for the unsubscribe link in one brief of the subscription. Only its hash is stored.

        None when the subscription is not active, so an ended subscription never gets a link.
        """
        token = secrets.token_urlsafe(TOKEN_BYTES)
        with closing(self._connect()) as db, db:
            # One statement checks the subscription and adds the link, so it cannot end in between.
            added = db.execute(
                "INSERT INTO unsubscribe_link (token_hash, instrument_id, email, created_at)"
                " SELECT ?, instrument_id, email, ? FROM subscription"
                " WHERE instrument_id = ? AND email = ? AND status = 'active'",
                (_hash(token), now.isoformat(), subscription.instrument_id, subscription.email),
            )
        return token if added.rowcount == 1 else None

    def check_unsubscribe(self, token: str) -> Subscription | UnsubscribeLinkProblem:
        """The active subscription `unsubscribe` would end with the token, or why it would not. Does not use it."""
        with closing(self._connect()) as db:
            return _unsubscribe_link_subscription(db, token)

    def unsubscribe(self, token: str, now: datetime) -> Subscription | UnsubscribeLinkProblem:
        """End the subscription the token's link is for, and only that one. Return it, or say why the link cannot
        end it."""
        with closing(self._connect()) as db, db:
            subscription = _unsubscribe_link_subscription(db, token)
            if isinstance(subscription, UnsubscribeLinkProblem):
                return subscription
            if not _end(db, subscription, now, _EndReason.UNSUBSCRIBED):
                # Another request ended it at the same moment.
                return _unsubscribe_link_subscription(db, token)
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
            return _end(db, subscription, now, _EndReason.GUARANTOR_CHANGED)


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


def _unsubscribe_link_subscription(db: sqlite3.Connection, token: str) -> Subscription | UnsubscribeLinkProblem:
    """The active subscription the token's link would end, or why the link cannot end one."""
    row: tuple[str, str, str | None, str | None] | None = db.execute(
        "SELECT instrument_id, email, used_at, end_reason FROM unsubscribe_link WHERE token_hash = ?", (_hash(token),)
    ).fetchone()
    if row is None:
        return UnsubscribeLinkProblem.UNKNOWN
    instrument_id, email, used_at, end_reason = row
    if used_at is not None:
        if end_reason == _EndReason.GUARANTOR_CHANGED.value:
            return UnsubscribeLinkProblem.GUARANTOR_CHANGED
        return UnsubscribeLinkProblem.ALREADY_UNSUBSCRIBED
    active = db.execute(
        "SELECT 1 FROM subscription WHERE instrument_id = ? AND email = ? AND status = 'active'",
        (instrument_id, email),
    ).fetchone()
    if active is None:
        # Not expected: ending a subscription uses up all its links. Safe either way, as the link ends nothing.
        return UnsubscribeLinkProblem.ALREADY_UNSUBSCRIBED
    return Subscription(instrument_id, email)


def _end(db: sqlite3.Connection, subscription: Subscription, now: datetime, reason: _EndReason) -> bool:
    """End an active subscription and use up all its unsubscribe links. False when it was not active.

    Every way of ending a subscription goes through here, so an old link can never end a later subscription to the
    same instrument, such as one the guarantor confirms again.
    """
    ended = db.execute(
        "UPDATE subscription SET status = 'ended', ended_at = ?"
        " WHERE instrument_id = ? AND email = ? AND status = 'active'",
        (now.isoformat(), subscription.instrument_id, subscription.email),
    )
    if ended.rowcount != 1:
        return False
    db.execute(
        "UPDATE unsubscribe_link SET used_at = ?, end_reason = ?"
        " WHERE instrument_id = ? AND email = ? AND used_at IS NULL",
        (now.isoformat(), reason.value, subscription.instrument_id, subscription.email),
    )
    return True


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
        if version < 3:
            _upgrade_to_3(db)
        _upgrade_to_4(db)
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


def _upgrade_to_4(db: sqlite3.Connection) -> None:
    """Add the unsubscribe_link table. The statement is version 4's own, as in _upgrade_to_2.

    IF NOT EXISTS: a database made before schema versions covered unsubscribe links may have the table already, at
    version 0, without `end_reason`. It keeps its rows and gains the column; its used links read as unsubscribed.
    """
    db.execute(
        """
CREATE TABLE IF NOT EXISTS unsubscribe_link (
    token_hash TEXT PRIMARY KEY,
    instrument_id TEXT NOT NULL,
    email TEXT NOT NULL,
    created_at TEXT NOT NULL,
    used_at TEXT,
    end_reason TEXT
)
"""
    )
    columns = {name for _, name, *_ in db.execute("PRAGMA table_info(unsubscribe_link)")}
    if "end_reason" not in columns:
        db.execute("ALTER TABLE unsubscribe_link ADD COLUMN end_reason TEXT")


def _version(db: sqlite3.Connection) -> int:
    [version] = db.execute("PRAGMA user_version").fetchone()
    return int(version)
