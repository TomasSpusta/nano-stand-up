from datetime import UTC, datetime, timedelta

import pytest

from nano_standup.booking_api import BookingApiError
from nano_standup.booking_source import FakeBookingSource
from nano_standup.domain import InstrumentName
from nano_standup.instrument_list import INSTRUMENT_LIST_MAX_AGE, InstrumentListCache

from helpers import INSTRUMENT

NOW = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
LISTED = [InstrumentName(INSTRUMENT.id, INSTRUMENT.name, INSTRUMENT.alias)]


class CountingSource(FakeBookingSource):
    """Counts the list reads, and fails them while `down` is set."""

    def __init__(self) -> None:
        super().__init__([INSTRUMENT])
        self.reads = 0
        self.down = False

    def list_subscribable_instruments(self) -> list[InstrumentName]:
        self.reads += 1
        if self.down:
            raise BookingApiError("/api/equipment", None, "Connection refused")
        return super().list_subscribable_instruments()


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def test_reads_the_list_once_while_it_is_fresh() -> None:
    source, clock = CountingSource(), Clock()
    cache = InstrumentListCache(source, clock)

    assert cache.get() == LISTED
    clock.now += INSTRUMENT_LIST_MAX_AGE - timedelta(seconds=1)
    assert cache.get() == LISTED

    assert source.reads == 1


def test_reads_the_list_again_when_it_is_old() -> None:
    source, clock = CountingSource(), Clock()
    cache = InstrumentListCache(source, clock)
    cache.get()

    clock.now += INSTRUMENT_LIST_MAX_AGE
    cache.get()

    assert source.reads == 2


def test_keeps_the_old_list_and_warns_when_a_refresh_fails(caplog: pytest.LogCaptureFixture) -> None:
    source, clock = CountingSource(), Clock()
    cache = InstrumentListCache(source, clock)
    cache.get()
    source.down = True
    clock.now += INSTRUMENT_LIST_MAX_AGE

    assert cache.get() == LISTED
    assert "Connection refused" in caplog.text
    assert "older instrument list" in caplog.text


def test_raises_when_there_is_no_list_at_all() -> None:
    source = CountingSource()
    source.down = True

    with pytest.raises(BookingApiError):
        InstrumentListCache(source, Clock()).get()
