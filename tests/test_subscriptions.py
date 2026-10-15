import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from nano_standup.subscriptions import Subscription, SubscriptionStore

NOW = datetime(2026, 10, 19, 6, 0, tzinfo=UTC)

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
        (1, "instr-1", "ended", "2026-10-01T06:00:00+00:00", "2026-10-19T06:00:00+00:00"),
        (2, "instr-2", "active", "2026-10-02T06:00:00+00:00", None),
    ]
    assert _user_version(database) == _user_version(_new_database(tmp_path)) == 2


def test_opening_an_up_to_date_database_needs_no_write_lock(tmp_path: Path) -> None:
    database = _new_database(tmp_path)
    with closing(sqlite3.connect(database, timeout=0)) as other:
        # Another process holds the write lock, as the Monday job does while it records a sent brief.
        other.execute("BEGIN IMMEDIATE")
        store = SubscriptionStore(database)
        other.rollback()

    assert store.list_active() == []


def _status_and_end_time(database: Path) -> list[tuple[str, str | None]]:
    with closing(sqlite3.connect(database)) as db:
        return db.execute("SELECT status, ended_at FROM subscription ORDER BY id").fetchall()


def _new_database(tmp_path: Path) -> Path:
    database = tmp_path / "new.sqlite3"
    SubscriptionStore(database)
    return database


def _user_version(database: Path) -> int:
    with closing(sqlite3.connect(database)) as db:
        [version] = db.execute("PRAGMA user_version").fetchone()
    return int(version)
