from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from nano_standup.brief import build_brief
from nano_standup.domain import Week

from helpers import INSTRUMENT, reservation, visible_text

WEEK = Week(date(2026, 10, 19))
BUILT_AT = datetime(2026, 10, 19, 8, 0, tzinfo=ZoneInfo("Europe/Prague"))


def test_subject_and_heading_name_the_instrument_and_the_week() -> None:
    brief = build_brief(INSTRUMENT, WEEK, [], BUILT_AT)

    assert brief.subject == "Brief: TEST-SEM - week 19.-25. 10. 2026"
    text = visible_text(brief.html)
    assert "Test Electron Microscope" in text
    assert "TEST-SEM - Monday 19. 10. to Sunday 25. 10. 2026" in text


def test_counts_show_reservations_hours_users_and_reservations_without_an_experiment_plan() -> None:
    reservations = [
        reservation("2026-10-19T09:00", "2026-10-19T12:00", user_name="Petr Vymysleny"),
        reservation("2026-10-20T13:00", "2026-10-20T14:30", user_name="Jana Smyslena", experiment_plan=""),
        reservation("2026-10-21T08:00", "2026-10-21T10:00", user_name="Petr Vymysleny", experiment_plan="  "),
    ]

    text = visible_text(build_brief(INSTRUMENT, WEEK, reservations, BUILT_AT).html)

    assert "3 reservations" in text
    assert "6.5 h booked" in text
    assert "2 users" in text
    assert "2 without an experiment plan" in text


def test_counts_use_singular_words_and_whole_hours() -> None:
    reservations = [reservation("2026-10-19T09:00", "2026-10-19T12:00")]

    text = visible_text(build_brief(INSTRUMENT, WEEK, reservations, BUILT_AT).html)

    assert "1 reservations" not in text
    assert "1 reservation " in text
    assert "3 h booked" in text
    assert "1 user " in text


def test_reservations_are_grouped_by_day_in_time_order() -> None:
    reservations = [
        reservation("2026-10-21T13:00", "2026-10-21T15:00", user_name="Jana Smyslena"),
        reservation("2026-10-19T14:00", "2026-10-19T16:00", user_name="Karel Neexistujici"),
        reservation("2026-10-19T08:30", "2026-10-19T11:00", user_name="Petr Vymysleny", project_code="2026-123456/RG-1-01"),
    ]

    text = visible_text(build_brief(INSTRUMENT, WEEK, reservations, BUILT_AT).html)

    monday = text.index("Monday 19. 10.", text.index("without an experiment plan"))
    first = text.index("08:30-11:00 Petr Vymysleny 2026-123456/RG-1-01 Imaging of samples")
    second = text.index("14:00-16:00 Karel Neexistujici")
    wednesday = text.index("Wednesday 21. 10.")
    third = text.index("13:00-15:00 Jana Smyslena")
    assert monday < first < second < wednesday < third


def test_a_reservation_without_an_experiment_plan_says_so() -> None:
    reservations = [reservation("2026-10-19T09:00", "2026-10-19T10:00", user_name="Jana Smyslena", experiment_plan=" ")]

    text = visible_text(build_brief(INSTRUMENT, WEEK, reservations, BUILT_AT).html)

    assert "09:00-10:00 Jana Smyslena 2026-000001/RG-1-01 No experiment plan given" in text


def test_footer_shows_the_build_time() -> None:
    late = datetime(2026, 10, 19, 9, 47, tzinfo=ZoneInfo("Europe/Prague"))

    text = visible_text(build_brief(INSTRUMENT, WEEK, [], late).html)

    assert "Built on Monday 19. 10. 2026 at 09:47." in text
    assert "Check the booking system for later changes." in text


def test_user_text_is_escaped() -> None:
    reservations = [reservation("2026-10-19T09:00", "2026-10-19T10:00", experiment_plan="<b>bold</b> & more")]

    html = build_brief(INSTRUMENT, WEEK, reservations, BUILT_AT).html

    assert "&lt;b&gt;bold&lt;/b&gt; &amp; more" in html


def test_subject_spans_two_months_when_the_week_does() -> None:
    brief = build_brief(INSTRUMENT, Week(date(2026, 9, 28)), [], BUILT_AT)

    assert brief.subject == "Brief: TEST-SEM - week 28. 9.-4. 10. 2026"


def test_users_without_an_id_still_count_once_per_name() -> None:
    reservations = [
        reservation("2026-10-19T09:00", "2026-10-19T10:00", user_name="Jana Smyslena", user_id=""),
        reservation("2026-10-20T09:00", "2026-10-20T10:00", user_name="Karel Neexistujici", user_id=""),
    ]

    text = visible_text(build_brief(INSTRUMENT, WEEK, reservations, BUILT_AT).html)

    assert "2 users" in text


def test_a_build_time_without_a_time_zone_is_refused() -> None:
    with pytest.raises(ValueError, match="built_at must carry a time zone, got 2026-10-19T08:00:00"):
        build_brief(INSTRUMENT, WEEK, [], datetime(2026, 10, 19, 8, 0))
