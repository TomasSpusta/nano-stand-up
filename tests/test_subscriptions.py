import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nano_standup.subscriptions import (
    CONFIRM_LINK_LIFETIME,
    ConfirmOutcome,
    Subscription,
    SubscriptionStore,
    UnsubscribeOutcome,
)

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
    pending = store.request(SEM, "  Hana.Testova@Example.org ", NOW)

    result = store.confirm(pending.token, NOW + timedelta(minutes=5))

    assert result.outcome is ConfirmOutcome.CONFIRMED
    assert result.subscription == Subscription(SEM, EMAIL)
    assert store.list_active() == [Subscription(SEM, EMAIL)]


def test_the_confirm_link_expires_after_its_lifetime(store: SubscriptionStore) -> None:
    pending = store.request(SEM, EMAIL, NOW)

    assert pending.expires_at == NOW + CONFIRM_LINK_LIFETIME
    result = store.confirm(pending.token, NOW + CONFIRM_LINK_LIFETIME)

    assert result.outcome is ConfirmOutcome.EXPIRED
    assert result.subscription is None
    assert store.list_active() == []


def test_the_confirm_link_works_just_before_it_expires(store: SubscriptionStore) -> None:
    pending = store.request(SEM, EMAIL, NOW)

    result = store.confirm(pending.token, NOW + CONFIRM_LINK_LIFETIME - timedelta(seconds=1))

    assert result.outcome is ConfirmOutcome.CONFIRMED


def test_the_confirm_link_works_only_once(store: SubscriptionStore) -> None:
    pending = store.request(SEM, EMAIL, NOW)
    store.confirm(pending.token, NOW)

    result = store.confirm(pending.token, NOW)

    assert result.outcome is ConfirmOutcome.USED
    assert result.subscription is None


def test_an_unknown_token_is_refused(store: SubscriptionStore) -> None:
    result = store.confirm("no-such-token", NOW)

    assert result.outcome is ConfirmOutcome.UNKNOWN
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
    pending = SubscriptionStore(path).request(SEM, EMAIL, NOW)

    with closing(sqlite3.connect(path)) as db:
        dump = "\n".join(db.iterdump())

    assert pending.token not in dump


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
    pending = store.request(SEM, EMAIL, NOW)
    store.confirm(pending.token, NOW)

    assert store.list_active() == [Subscription(LAB, EMAIL), Subscription(SEM, EMAIL)]


def test_checking_a_link_does_not_use_it(store: SubscriptionStore) -> None:
    pending = store.request(SEM, EMAIL, NOW)

    assert store.check(pending.token, NOW) is ConfirmOutcome.READY
    assert store.check(pending.token, NOW) is ConfirmOutcome.READY
    assert store.list_active() == []
    assert store.confirm(pending.token, NOW).outcome is ConfirmOutcome.CONFIRMED


def test_checking_a_link_tells_expired_used_and_unknown(store: SubscriptionStore) -> None:
    used = store.request(SEM, EMAIL, NOW)
    store.confirm(used.token, NOW)
    expired = store.request(LAB, EMAIL, NOW)

    assert store.check(used.token, NOW) is ConfirmOutcome.USED
    assert store.check(expired.token, NOW + CONFIRM_LINK_LIFETIME) is ConfirmOutcome.EXPIRED
    assert store.check("no-such-token", NOW) is ConfirmOutcome.UNKNOWN


def test_a_recent_request_is_one_still_valid_and_made_since_the_given_time(store: SubscriptionStore) -> None:
    pending = store.request(SEM, EMAIL, NOW)

    assert store.has_recent_request(SEM, since=NOW - timedelta(minutes=5), now=NOW + timedelta(minutes=1))
    assert not store.has_recent_request(SEM, since=NOW + timedelta(seconds=1), now=NOW + timedelta(minutes=1))
    assert not store.has_recent_request(LAB, since=NOW - timedelta(minutes=5), now=NOW)
    store.confirm(pending.token, NOW)
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
    token = store.unsubscribe_token(sem, NOW)

    result = store.unsubscribe(token, NOW)

    assert result.outcome is UnsubscribeOutcome.UNSUBSCRIBED
    assert result.subscription == sem
    assert store.list_active() == [Subscription(LAB, EMAIL), Subscription(SEM, "ota.zkusebni@example.org")]


def test_unsubscribing_again_says_already_unsubscribed(store: SubscriptionStore) -> None:
    token = store.unsubscribe_token(store.add_active(SEM, EMAIL, NOW), NOW)
    store.unsubscribe(token, NOW)

    result = store.unsubscribe(token, NOW)

    assert result.outcome is UnsubscribeOutcome.ALREADY_UNSUBSCRIBED
    assert result.subscription is None


def test_every_unsubscribe_link_of_a_subscription_works_until_one_is_used(store: SubscriptionStore) -> None:
    sem = store.add_active(SEM, EMAIL, NOW)
    # One link per brief: an old brief, read weeks later, still has a working link.
    old = store.unsubscribe_token(sem, NOW)
    new = store.unsubscribe_token(sem, NOW + timedelta(weeks=1))

    assert old != new
    assert store.check_unsubscribe(old) is UnsubscribeOutcome.READY
    store.unsubscribe(new, NOW + timedelta(weeks=8))

    assert store.check_unsubscribe(old) is UnsubscribeOutcome.ALREADY_UNSUBSCRIBED
    assert store.unsubscribe(old, NOW).outcome is UnsubscribeOutcome.ALREADY_UNSUBSCRIBED


def test_an_unsubscribe_link_from_before_a_new_subscription_does_not_end_it(store: SubscriptionStore) -> None:
    sem = store.add_active(SEM, EMAIL, NOW)
    old = store.unsubscribe_token(sem, NOW)
    store.unsubscribe(store.unsubscribe_token(sem, NOW), NOW)
    store.add_active(SEM, EMAIL, NOW + timedelta(days=1))

    result = store.unsubscribe(old, NOW + timedelta(days=2))

    assert result.outcome is UnsubscribeOutcome.ALREADY_UNSUBSCRIBED
    assert store.list_active() == [sem]


def test_checking_an_unsubscribe_link_does_not_use_it(store: SubscriptionStore) -> None:
    sem = store.add_active(SEM, EMAIL, NOW)
    token = store.unsubscribe_token(sem, NOW)

    assert store.check_unsubscribe(token) is UnsubscribeOutcome.READY
    assert store.check_unsubscribe(token) is UnsubscribeOutcome.READY
    assert store.list_active() == [sem]


def test_an_unknown_unsubscribe_token_is_refused(store: SubscriptionStore) -> None:
    store.add_active(SEM, EMAIL, NOW)

    assert store.check_unsubscribe("no-such-token") is UnsubscribeOutcome.UNKNOWN
    assert store.unsubscribe("no-such-token", NOW).outcome is UnsubscribeOutcome.UNKNOWN
    assert store.list_active() == [Subscription(SEM, EMAIL)]


def test_unsubscribe_tokens_are_random_and_only_their_hash_is_kept(tmp_path: Path) -> None:
    path = tmp_path / "standup.sqlite3"
    store = SubscriptionStore(path)
    sem = store.add_active(SEM, EMAIL, NOW)
    tokens = {store.unsubscribe_token(sem, NOW) for _ in range(20)}

    with closing(sqlite3.connect(path)) as db:
        dump = "\n".join(db.iterdump())

    assert len(tokens) == 20
    assert all(len(token) >= 43 for token in tokens)
    assert not any(token in dump for token in tokens)
