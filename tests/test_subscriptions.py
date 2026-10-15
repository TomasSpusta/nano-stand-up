import hashlib
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nano_standup.subscriptions import (
    CONFIRM_LINK_LIFETIME,
    ConfirmLinkProblem,
    Subscription,
    SubscriptionStore,
    UnsubscribeLinkProblem,
)

NOW = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
SEM = "equip-sem"
LAB = "equip-lab"
EMAIL = "hana.testova@example.org"

# The exact statement the first schema version (user_version 0) used to create the table.
_FIRST_SCHEMA = """
CREATE TABLE IF NOT EXISTS subscription (
    id INTEGER PRIMARY KEY,
    instrument_id TEXT NOT NULL,
    email TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active')),
    created_at TEXT NOT NULL,
    UNIQUE (instrument_id, email)
)
"""

# The exact statement a database made before schema versions covered confirm links used for that table.
_UNVERSIONED_CONFIRM_LINK_SCHEMA = """
CREATE TABLE IF NOT EXISTS confirm_link (
    token_hash TEXT PRIMARY KEY,
    instrument_id TEXT NOT NULL,
    email TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT
)
"""

# The exact statement a database made before schema versions covered unsubscribe links used for that table.
_UNVERSIONED_UNSUBSCRIBE_LINK_SCHEMA = """
CREATE TABLE IF NOT EXISTS unsubscribe_link (
    token_hash TEXT PRIMARY KEY,
    instrument_id TEXT NOT NULL,
    email TEXT NOT NULL,
    created_at TEXT NOT NULL,
    used_at TEXT
)
"""

# The exact statement schema version 2 used to create the table. Version 2 had no confirm_link table.
_SECOND_SCHEMA = """
CREATE TABLE subscription (
    id INTEGER PRIMARY KEY,
    instrument_id TEXT NOT NULL,
    email TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'ended')),
    created_at TEXT NOT NULL,
    ended_at TEXT,
    UNIQUE (instrument_id, email)
)
"""


@pytest.fixture
def store(tmp_path: Path) -> SubscriptionStore:
    return SubscriptionStore(tmp_path / "standup.sqlite3")


def test_a_requested_subscription_is_not_active_before_it_is_confirmed(store: SubscriptionStore) -> None:
    store.request(SEM, EMAIL, NOW)

    assert store.list_active() == []


def test_confirming_makes_the_subscription_active_with_the_normalized_email(store: SubscriptionStore) -> None:
    requested = store.request(SEM, "  Hana.Testova@Example.org ", NOW)

    result = store.confirm(requested.token, NOW + timedelta(minutes=5))

    assert result == Subscription(SEM, EMAIL)
    assert store.list_active() == [Subscription(SEM, EMAIL)]


def test_the_confirm_link_expires_after_its_lifetime(store: SubscriptionStore) -> None:
    requested = store.request(SEM, EMAIL, NOW)

    assert requested.expires_at == NOW + CONFIRM_LINK_LIFETIME
    result = store.confirm(requested.token, NOW + CONFIRM_LINK_LIFETIME)

    assert result is ConfirmLinkProblem.EXPIRED
    assert store.list_active() == []


def test_the_confirm_link_works_just_before_it_expires(store: SubscriptionStore) -> None:
    requested = store.request(SEM, EMAIL, NOW)

    result = store.confirm(requested.token, NOW + CONFIRM_LINK_LIFETIME - timedelta(seconds=1))

    assert result == Subscription(SEM, EMAIL)


def test_the_confirm_link_works_only_once(store: SubscriptionStore) -> None:
    requested = store.request(SEM, EMAIL, NOW)
    store.confirm(requested.token, NOW)

    result = store.confirm(requested.token, NOW)

    assert result is ConfirmLinkProblem.USED


def test_two_confirms_of_one_token_at_the_same_moment_activate_it_once(tmp_path: Path) -> None:
    # Two stores on one file, like two web requests in their own threads, such as a double click on Confirm.
    path = tmp_path / "standup.sqlite3"
    stores = [SubscriptionStore(path), SubscriptionStore(path)]
    requested = stores[0].request(SEM, EMAIL, NOW)
    start = threading.Barrier(len(stores))

    def confirm(store: SubscriptionStore) -> Subscription | ConfirmLinkProblem:
        start.wait(timeout=5)
        return store.confirm(requested.token, NOW)

    with ThreadPoolExecutor(len(stores)) as pool:
        results = list(pool.map(confirm, stores))

    assert sorted(results, key=str) == [ConfirmLinkProblem.USED, Subscription(SEM, EMAIL)]
    assert stores[0].list_active() == [Subscription(SEM, EMAIL)]


def test_an_unknown_token_is_refused(store: SubscriptionStore) -> None:
    result = store.confirm("no-such-token", NOW)

    assert result is ConfirmLinkProblem.UNKNOWN
    assert store.list_active() == []


def test_each_request_gets_its_own_random_token(store: SubscriptionStore) -> None:
    tokens = {store.request(SEM, EMAIL, NOW).token for _ in range(20)}

    assert len(tokens) == 20
    # Long enough that no one can guess one: secrets.token_urlsafe(32) gives 43 characters.
    assert all(len(token) >= 43 for token in tokens)


def test_confirming_the_same_instrument_twice_keeps_one_subscription(store: SubscriptionStore) -> None:
    first = store.request(SEM, EMAIL, NOW)
    second = store.request(SEM, "HANA.TESTOVA@example.org", NOW)

    store.confirm(first.token, NOW)
    store.confirm(second.token, NOW)

    assert store.list_active() == [Subscription(SEM, EMAIL)]


def test_one_guarantor_subscribes_to_each_instrument_on_its_own(store: SubscriptionStore) -> None:
    sem = store.request(SEM, EMAIL, NOW)
    store.request(LAB, EMAIL, NOW)

    store.confirm(sem.token, NOW)

    assert store.list_active() == [Subscription(SEM, EMAIL)]


def test_the_database_keeps_only_a_hash_of_the_token(tmp_path: Path) -> None:
    path = tmp_path / "standup.sqlite3"
    requested = SubscriptionStore(path).request(SEM, EMAIL, NOW)

    with closing(sqlite3.connect(path)) as db:
        dump = "\n".join(db.iterdump())

    assert requested.token not in dump


def test_checking_a_link_does_not_use_it(store: SubscriptionStore) -> None:
    requested = store.request(SEM, EMAIL, NOW)

    assert store.check(requested.token, NOW) == Subscription(SEM, EMAIL)
    assert store.check(requested.token, NOW) == Subscription(SEM, EMAIL)
    assert store.list_active() == []
    assert store.confirm(requested.token, NOW) == Subscription(SEM, EMAIL)


def test_checking_a_link_tells_expired_used_and_unknown(store: SubscriptionStore) -> None:
    used = store.request(SEM, EMAIL, NOW)
    store.confirm(used.token, NOW)
    expired = store.request(LAB, EMAIL, NOW)

    assert store.check(used.token, NOW) is ConfirmLinkProblem.USED
    assert store.check(expired.token, NOW + CONFIRM_LINK_LIFETIME) is ConfirmLinkProblem.EXPIRED
    assert store.check("no-such-token", NOW) is ConfirmLinkProblem.UNKNOWN


def test_a_recent_request_is_one_still_valid_and_made_since_the_given_time(store: SubscriptionStore) -> None:
    requested = store.request(SEM, EMAIL, NOW)

    assert store.has_recent_request(SEM, since=NOW - timedelta(minutes=5), now=NOW + timedelta(minutes=1))
    assert not store.has_recent_request(SEM, since=NOW + timedelta(seconds=1), now=NOW + timedelta(minutes=1))
    assert not store.has_recent_request(LAB, since=NOW - timedelta(minutes=5), now=NOW)
    store.confirm(requested.token, NOW)
    assert not store.has_recent_request(SEM, since=NOW - timedelta(minutes=5), now=NOW)


def test_an_expired_request_is_not_recent(tmp_path: Path) -> None:
    store = SubscriptionStore(tmp_path / "standup.sqlite3", confirm_link_lifetime=timedelta(minutes=1))
    store.request(SEM, EMAIL, NOW)

    assert not store.has_recent_request(SEM, since=NOW - timedelta(minutes=5), now=NOW + timedelta(minutes=2))


# Unsubscribe


def test_unsubscribing_ends_only_that_subscription(store: SubscriptionStore) -> None:
    sem = store.add_active(SEM, EMAIL, NOW)
    store.add_active(LAB, EMAIL, NOW)
    store.add_active(SEM, "ota.zkusebni@example.org", NOW)
    token = _unsubscribe_token(store, sem, NOW)

    assert store.unsubscribe(token, NOW) == sem
    assert store.list_active() == [Subscription(LAB, EMAIL), Subscription(SEM, "ota.zkusebni@example.org")]


def test_unsubscribing_again_says_already_unsubscribed(store: SubscriptionStore) -> None:
    token = _unsubscribe_token(store, store.add_active(SEM, EMAIL, NOW), NOW)
    store.unsubscribe(token, NOW)

    assert store.unsubscribe(token, NOW) is UnsubscribeLinkProblem.ALREADY_UNSUBSCRIBED


def test_every_unsubscribe_link_of_a_subscription_works_until_one_is_used(store: SubscriptionStore) -> None:
    sem = store.add_active(SEM, EMAIL, NOW)
    # One link per brief: an old brief, read weeks later, still has a working link.
    old = _unsubscribe_token(store, sem, NOW)
    new = _unsubscribe_token(store, sem, NOW + timedelta(weeks=1))

    assert old != new
    assert store.check_unsubscribe(old) == sem
    store.unsubscribe(new, NOW + timedelta(weeks=8))

    assert store.check_unsubscribe(old) is UnsubscribeLinkProblem.ALREADY_UNSUBSCRIBED
    assert store.unsubscribe(old, NOW) is UnsubscribeLinkProblem.ALREADY_UNSUBSCRIBED


def test_an_unsubscribe_link_from_before_a_new_subscription_does_not_end_it(store: SubscriptionStore) -> None:
    sem = store.add_active(SEM, EMAIL, NOW)
    old = _unsubscribe_token(store, sem, NOW)
    store.unsubscribe(_unsubscribe_token(store, sem, NOW), NOW)
    store.add_active(SEM, EMAIL, NOW + timedelta(days=1))

    assert store.unsubscribe(old, NOW + timedelta(days=2)) is UnsubscribeLinkProblem.ALREADY_UNSUBSCRIBED
    assert store.list_active() == [sem]


def test_checking_an_unsubscribe_link_does_not_use_it(store: SubscriptionStore) -> None:
    sem = store.add_active(SEM, EMAIL, NOW)
    token = _unsubscribe_token(store, sem, NOW)

    assert store.check_unsubscribe(token) == sem
    assert store.check_unsubscribe(token) == sem
    assert store.list_active() == [sem]


def test_an_unknown_unsubscribe_token_is_refused(store: SubscriptionStore) -> None:
    store.add_active(SEM, EMAIL, NOW)

    assert store.check_unsubscribe("no-such-token") is UnsubscribeLinkProblem.UNKNOWN
    assert store.unsubscribe("no-such-token", NOW) is UnsubscribeLinkProblem.UNKNOWN
    assert store.list_active() == [Subscription(SEM, EMAIL)]


def test_unsubscribe_tokens_are_random_and_only_their_hash_is_kept(tmp_path: Path) -> None:
    path = tmp_path / "standup.sqlite3"
    store = SubscriptionStore(path)
    sem = store.add_active(SEM, EMAIL, NOW)
    tokens = {_unsubscribe_token(store, sem, NOW) for _ in range(20)}

    with closing(sqlite3.connect(path)) as db:
        dump = "\n".join(db.iterdump())

    assert len(tokens) == 20
    assert all(len(token) >= 43 for token in tokens)
    assert not any(token in dump for token in tokens)


def test_ending_a_subscription_because_the_guarantor_changed_stops_it_once(tmp_path: Path) -> None:
    store = SubscriptionStore(tmp_path / "standup.sqlite3")
    hana = store.add_active("instr-1", "hana.testova@example.org", NOW)
    ota = store.add_active("instr-2", "ota.zkusebni@example.org", NOW)

    assert store.end_because_guarantor_changed(hana, NOW) is True
    # Already ended, so ending it again changes nothing.
    assert store.end_because_guarantor_changed(hana, NOW) is False
    assert store.list_active() == [ota]


def test_a_subscription_ended_can_be_added_again(tmp_path: Path) -> None:
    store = SubscriptionStore(tmp_path / "standup.sqlite3")
    hana = store.add_active("instr-1", "hana.testova@example.org", NOW)
    store.end_because_guarantor_changed(hana, NOW)

    store.add_active("instr-1", "Hana.Testova@example.org", NOW)

    assert store.list_active() == [hana]
    # Active again, so it has no end time. The store has no public read for it, so the test reads the row.
    assert _status_and_end_time(tmp_path / "standup.sqlite3") == [("active", None)]


def test_a_database_from_before_ended_subscriptions_is_upgraded_and_keeps_its_subscriptions(tmp_path: Path) -> None:
    database = tmp_path / "standup.sqlite3"
    with closing(sqlite3.connect(database)) as db, db:
        db.execute(_FIRST_SCHEMA)
        db.execute(
            "INSERT INTO subscription (instrument_id, email, status, created_at) VALUES"
            " ('instr-1', 'hana.testova@example.org', 'active', '2026-10-01T06:00:00+00:00'),"
            " ('instr-2', 'ota.zkusebni@example.org', 'active', '2026-10-02T06:00:00+00:00')"
        )

    store = SubscriptionStore(database)
    ended = store.end_because_guarantor_changed(Subscription("instr-1", "hana.testova@example.org"), NOW)

    assert ended is True
    assert store.list_active() == [Subscription("instr-2", "ota.zkusebni@example.org")]
    with closing(sqlite3.connect(database)) as db:
        rows = db.execute(
            "SELECT id, instrument_id, status, created_at, ended_at FROM subscription ORDER BY id"
        ).fetchall()
    assert rows == [
        (1, "instr-1", "ended", "2026-10-01T06:00:00+00:00", "2026-10-14T10:00:00+00:00"),
        (2, "instr-2", "active", "2026-10-02T06:00:00+00:00", None),
    ]
    assert _user_version(database) == _user_version(_new_database(tmp_path)) == 4
    requested = store.request("instr-1", "hana.testova@example.org", NOW)
    assert store.confirm(requested.token, NOW) == Subscription("instr-1", "hana.testova@example.org")


def test_a_version_2_database_gains_confirm_links_and_keeps_its_subscriptions(tmp_path: Path) -> None:
    database = tmp_path / "standup.sqlite3"
    with closing(sqlite3.connect(database)) as db, db:
        db.execute(_SECOND_SCHEMA)
        db.execute(
            "INSERT INTO subscription (instrument_id, email, status, created_at, ended_at) VALUES"
            " ('instr-1', 'hana.testova@example.org', 'ended',"
            " '2026-10-01T06:00:00+00:00', '2026-10-12T06:00:00+00:00'),"
            " ('instr-2', 'ota.zkusebni@example.org', 'active', '2026-10-02T06:00:00+00:00', NULL)"
        )
        db.execute("PRAGMA user_version = 2")

    store = SubscriptionStore(database)

    assert store.list_active() == [Subscription("instr-2", "ota.zkusebni@example.org")]
    assert _user_version(database) == 4
    assert _table_statements(database) == _table_statements(_new_database(tmp_path))
    requested = store.request("instr-1", "hana.testova@example.org", NOW)
    assert store.confirm(requested.token, NOW) == Subscription("instr-1", "hana.testova@example.org")
    assert _status_and_end_time(database) == [("active", None), ("active", None)]


def test_a_version_0_database_that_has_confirm_links_keeps_them(tmp_path: Path) -> None:
    # Made before schema versions covered confirm links: the first subscription table and a confirm_link table.
    database = tmp_path / "standup.sqlite3"
    token = "a-token-sent-before-the-upgrade"
    with closing(sqlite3.connect(database)) as db, db:
        db.execute(_FIRST_SCHEMA)
        db.execute(_UNVERSIONED_CONFIRM_LINK_SCHEMA)
        db.execute(
            "INSERT INTO confirm_link (token_hash, instrument_id, email, created_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                hashlib.sha256(token.encode()).hexdigest(),
                SEM,
                EMAIL,
                NOW.isoformat(),
                (NOW + CONFIRM_LINK_LIFETIME).isoformat(),
            ),
        )

    store = SubscriptionStore(database)

    assert _user_version(database) == 4
    assert store.confirm(token, NOW) == Subscription(SEM, EMAIL)
    assert store.list_active() == [Subscription(SEM, EMAIL)]


def test_confirming_starts_an_ended_subscription_again(store: SubscriptionStore, tmp_path: Path) -> None:
    hana = store.add_active(SEM, EMAIL, NOW)
    store.end_because_guarantor_changed(hana, NOW)
    requested = store.request(SEM, EMAIL, NOW + timedelta(days=1))

    assert store.confirm(requested.token, NOW + timedelta(days=1)) == hana
    assert store.list_active() == [hana]
    assert _status_and_end_time(tmp_path / "standup.sqlite3") == [("active", None)]


def test_opening_an_up_to_date_database_needs_no_write_lock(tmp_path: Path) -> None:
    database = _new_database(tmp_path)
    with closing(sqlite3.connect(database, timeout=0)) as other:
        # Another process holds the write lock, as the Monday job does while it records a sent brief.
        other.execute("BEGIN IMMEDIATE")
        store = SubscriptionStore(database)
        other.rollback()

    assert store.list_active() == []


def test_unsubscribing_ends_the_subscription_and_keeps_its_row_with_the_end_time(tmp_path: Path) -> None:
    store = SubscriptionStore(tmp_path / "standup.sqlite3")
    sem = store.add_active(SEM, EMAIL, NOW)

    store.unsubscribe(_unsubscribe_token(store, sem, NOW), NOW + timedelta(days=3))

    assert _status_and_end_time(tmp_path / "standup.sqlite3") == [("ended", "2026-10-17T10:00:00+00:00")]


def test_an_ended_subscription_gets_no_unsubscribe_token(store: SubscriptionStore) -> None:
    sem = store.add_active(SEM, EMAIL, NOW)
    store.end_because_guarantor_changed(sem, NOW)

    assert store.unsubscribe_token(sem, NOW) is None
    assert store.unsubscribe_token(Subscription(LAB, EMAIL), NOW) is None


def test_ending_because_the_guarantor_changed_uses_up_every_unsubscribe_link(store: SubscriptionStore) -> None:
    sem = store.add_active(SEM, EMAIL, NOW)
    lab = store.add_active(LAB, EMAIL, NOW)
    old = _unsubscribe_token(store, sem, NOW)
    new = _unsubscribe_token(store, sem, NOW + timedelta(weeks=1))
    other = _unsubscribe_token(store, lab, NOW)

    store.end_because_guarantor_changed(sem, NOW + timedelta(weeks=2))

    for token in (old, new):
        assert store.check_unsubscribe(token) is UnsubscribeLinkProblem.GUARANTOR_CHANGED
        assert store.unsubscribe(token, NOW) is UnsubscribeLinkProblem.GUARANTOR_CHANGED
    # Only that subscription's links.
    assert store.check_unsubscribe(other) == lab


def test_an_unsubscribe_link_from_before_the_guarantor_changed_does_not_end_a_new_subscription(
    store: SubscriptionStore,
) -> None:
    sem = store.add_active(SEM, EMAIL, NOW)
    old = _unsubscribe_token(store, sem, NOW)
    store.end_because_guarantor_changed(sem, NOW + timedelta(weeks=1))
    # The guarantor confirms again.
    requested = store.request(SEM, EMAIL, NOW + timedelta(weeks=2))
    store.confirm(requested.token, NOW + timedelta(weeks=2))

    assert store.unsubscribe(old, NOW + timedelta(weeks=3)) is UnsubscribeLinkProblem.GUARANTOR_CHANGED
    assert store.list_active() == [sem]


def test_a_version_3_database_gains_unsubscribe_links_and_keeps_its_rows(tmp_path: Path) -> None:
    database = tmp_path / "standup.sqlite3"
    with closing(sqlite3.connect(database)) as db, db:
        db.execute(_SECOND_SCHEMA)
        db.execute(_UNVERSIONED_CONFIRM_LINK_SCHEMA)
        db.execute(
            "INSERT INTO subscription (instrument_id, email, status, created_at, ended_at) VALUES"
            " ('equip-sem', 'hana.testova@example.org', 'active', '2026-10-01T06:00:00+00:00', NULL)"
        )
        db.execute("PRAGMA user_version = 3")

    store = SubscriptionStore(database)

    assert _user_version(database) == 4
    assert _table_statements(database) == _table_statements(_new_database(tmp_path))
    sem = Subscription(SEM, EMAIL)
    assert store.list_active() == [sem]
    assert store.unsubscribe(_unsubscribe_token(store, sem, NOW), NOW) == sem


def test_a_version_0_database_from_before_the_upgrade_steps_reaches_the_latest_version(tmp_path: Path) -> None:
    database = tmp_path / "standup.sqlite3"
    with closing(sqlite3.connect(database)) as db, db:
        db.execute(_FIRST_SCHEMA)
        db.execute(
            "INSERT INTO subscription (instrument_id, email, status, created_at)"
            " VALUES ('equip-sem', 'hana.testova@example.org', 'active', '2026-10-01T06:00:00+00:00')"
        )

    store = SubscriptionStore(database)

    assert _user_version(database) == 4
    sem = Subscription(SEM, EMAIL)
    assert store.unsubscribe(_unsubscribe_token(store, sem, NOW), NOW) == sem


def test_a_version_0_database_that_has_unsubscribe_links_keeps_them(tmp_path: Path) -> None:
    # Made before schema versions covered unsubscribe links: the first subscription table, a confirm_link table,
    # and an unsubscribe_link table without end_reason.
    database = tmp_path / "standup.sqlite3"
    working = "a-link-sent-before-the-upgrade"
    used = "a-link-used-before-the-upgrade"
    with closing(sqlite3.connect(database)) as db, db:
        db.execute(_FIRST_SCHEMA)
        db.execute(_UNVERSIONED_CONFIRM_LINK_SCHEMA)
        db.execute(_UNVERSIONED_UNSUBSCRIBE_LINK_SCHEMA)
        db.execute(
            "INSERT INTO subscription (instrument_id, email, status, created_at) VALUES (?, ?, 'active', ?)",
            (SEM, EMAIL, NOW.isoformat()),
        )
        db.executemany(
            "INSERT INTO unsubscribe_link (token_hash, instrument_id, email, created_at, used_at)"
            " VALUES (?, ?, ?, ?, ?)",
            [
                (hashlib.sha256(working.encode()).hexdigest(), SEM, EMAIL, NOW.isoformat(), None),
                (hashlib.sha256(used.encode()).hexdigest(), LAB, EMAIL, NOW.isoformat(), NOW.isoformat()),
            ],
        )

    store = SubscriptionStore(database)

    assert _user_version(database) == 4
    assert store.check_unsubscribe(used) is UnsubscribeLinkProblem.ALREADY_UNSUBSCRIBED
    assert store.unsubscribe(working, NOW) == Subscription(SEM, EMAIL)
    assert store.check_unsubscribe(working) is UnsubscribeLinkProblem.ALREADY_UNSUBSCRIBED


def _unsubscribe_token(store: SubscriptionStore, subscription: Subscription, now: datetime) -> str:
    token = store.unsubscribe_token(subscription, now)
    assert token is not None
    return token


def _status_and_end_time(database: Path) -> list[tuple[str, str | None]]:
    with closing(sqlite3.connect(database)) as db:
        return db.execute("SELECT status, ended_at FROM subscription ORDER BY id").fetchall()


def _new_database(tmp_path: Path) -> Path:
    database = tmp_path / "new.sqlite3"
    SubscriptionStore(database)
    return database


def _table_statements(database: Path) -> list[str]:
    """The CREATE statements of the tables, with spacing made the same."""
    with closing(sqlite3.connect(database)) as db:
        rows = db.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall()
    return [" ".join(sql.split()) for _, sql in rows]


def _user_version(database: Path) -> int:
    with closing(sqlite3.connect(database)) as db:
        [version] = db.execute("PRAGMA user_version").fetchone()
    return int(version)
