from datetime import date, timedelta

import pytest

from nano_standup.domain import Week

from helpers import reservation

WEEK = Week(date(2026, 10, 19))


def test_a_week_must_start_on_a_monday() -> None:
    with pytest.raises(ValueError, match="Week must start on a Monday, got Tuesday 2026-10-20"):
        Week(date(2026, 10, 20))


def test_the_week_containing_a_day_starts_on_its_monday() -> None:
    assert Week.containing(date(2026, 10, 25)) == Week(date(2026, 10, 19))


@pytest.mark.usefixtures("czech_time_language")
def test_a_week_error_names_the_day_in_english_on_a_server_set_to_czech() -> None:
    with pytest.raises(ValueError, match="got Tuesday 2026-10-20"):
        Week(date(2026, 10, 20))


@pytest.mark.parametrize(
    ("start", "end", "inside"),
    [
        ("2026-10-18T22:00", "2026-10-19T00:00", False),
        ("2026-10-18T22:00", "2026-10-19T06:00", True),
        ("2026-10-25T20:00", "2026-10-26T02:00", True),
        ("2026-10-26T00:00", "2026-10-26T02:00", False),
    ],
)
def test_a_week_holds_a_reservation_with_any_time_inside_it(start: str, end: str, inside: bool) -> None:
    assert WEEK.overlaps(reservation(start, end)) is inside


@pytest.mark.parametrize(
    ("start", "end", "hours"),
    [
        ("2026-10-18T22:00", "2026-10-19T06:00", 6),
        ("2026-10-25T20:00", "2026-10-26T02:00", 4),
        ("2026-10-18T20:00", "2026-10-26T02:00", 168),
        ("2026-10-20T09:00", "2026-10-20T12:00", 3),
        ("2026-10-18T22:00", "2026-10-19T00:00", 0),
    ],
)
def test_time_inside_a_week_leaves_out_time_before_monday_and_after_sunday(start: str, end: str, hours: int) -> None:
    assert WEEK.time_inside(reservation(start, end)) == timedelta(hours=hours)
