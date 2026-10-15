import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nano_standup.subscriptions import CONFIRM_LINK_LIFETIME, ConfirmLinkProblem, Subscription, SubscriptionStore

NOW = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
SEM = "equip-sem"
LAB = "equip-lab"
EMAIL = "hana.testova@example.org"


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


def test_opens_a_database_made_before_confirm_links_existed(tmp_path: Path) -> None:
    path = tmp_path / "standup.sqlite3"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "CREATE TABLE subscription (id INTEGER PRIMARY KEY, instrument_id TEXT NOT NULL, email TEXT NOT NULL,"
            " status TEXT NOT NULL CHECK (status IN ('active')), created_at TEXT NOT NULL,"
            " UNIQUE (instrument_id, email))"
        )
        db.execute(
            "INSERT INTO subscription (instrument_id, email, status, created_at) VALUES (?, ?, 'active', ?)",
            (LAB, EMAIL, NOW.isoformat()),
        )

    store = SubscriptionStore(path)
    requested = store.request(SEM, EMAIL, NOW)
    store.confirm(requested.token, NOW)

    assert store.list_active() == [Subscription(LAB, EMAIL), Subscription(SEM, EMAIL)]


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
