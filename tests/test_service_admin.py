from datetime import UTC, datetime
from pathlib import Path

import pytest

from nano_standup.service_admin import NotTheGuarantor, add_subscription
from nano_standup.booking_source import FakeBookingSource, InstrumentNotFound
from nano_standup.subscriptions import Subscription, SubscriptionStore

from helpers import INSTRUMENT

NOW = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)


def test_adds_an_active_subscription_for_the_guarantor_email(tmp_path: Path) -> None:
    store = SubscriptionStore(tmp_path / "standup.sqlite3")

    add_subscription(FakeBookingSource([INSTRUMENT]), store, INSTRUMENT.id, "  Hana.Testova@Example.org ", NOW)

    assert store.list_active() == [Subscription(INSTRUMENT.id, "hana.testova@example.org")]


def test_adding_the_same_subscription_twice_keeps_one(tmp_path: Path) -> None:
    store = SubscriptionStore(tmp_path / "standup.sqlite3")
    source = FakeBookingSource([INSTRUMENT])

    add_subscription(source, store, INSTRUMENT.id, "hana.testova@example.org", NOW)
    add_subscription(source, store, INSTRUMENT.id, "HANA.TESTOVA@example.org", NOW)

    assert len(store.list_active()) == 1


def test_refuses_an_email_that_is_not_the_guarantor_email(tmp_path: Path) -> None:
    store = SubscriptionStore(tmp_path / "standup.sqlite3")

    with pytest.raises(NotTheGuarantor, match="TEST-SEM"):
        add_subscription(FakeBookingSource([INSTRUMENT]), store, INSTRUMENT.id, "someone@example.org", NOW)

    assert store.list_active() == []


def test_refuses_an_unknown_instrument(tmp_path: Path) -> None:
    store = SubscriptionStore(tmp_path / "standup.sqlite3")

    with pytest.raises(InstrumentNotFound):
        add_subscription(FakeBookingSource([INSTRUMENT]), store, "no-such-id", "hana.testova@example.org", NOW)

    assert store.list_active() == []
