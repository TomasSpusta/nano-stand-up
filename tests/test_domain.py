from datetime import date

import pytest

from nano_standup.domain import Week


def test_a_week_must_start_on_a_monday() -> None:
    with pytest.raises(ValueError, match="Week must start on a Monday, got Tuesday 2026-10-20"):
        Week(date(2026, 10, 20))


def test_the_week_containing_a_day_starts_on_its_monday() -> None:
    assert Week.containing(date(2026, 10, 25)) == Week(date(2026, 10, 19))


@pytest.mark.usefixtures("czech_time_language")
def test_a_week_error_names_the_day_in_english_on_a_server_set_to_czech() -> None:
    with pytest.raises(ValueError, match="got Tuesday 2026-10-20"):
        Week(date(2026, 10, 20))
