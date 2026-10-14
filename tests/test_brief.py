from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from nano_standup.brief import build_brief
from nano_standup.domain import ReservationStatus, Week

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
    first = text.index("08:30 to 11:00 2.5 h Petr Vymysleny 2026-123456/RG-1-01 Imaging of samples")
    second = text.index("14:00 to 16:00 2 h Karel Neexistujici")
    wednesday = text.index("Wednesday 21. 10.")
    third = text.index("13:00 to 15:00 2 h Jana Smyslena")
    assert monday < first < second < wednesday < third


def test_a_reservation_without_an_experiment_plan_says_so() -> None:
    reservations = [reservation("2026-10-19T09:00", "2026-10-19T10:00", user_name="Jana Smyslena", experiment_plan=" ")]

    text = visible_text(build_brief(INSTRUMENT, WEEK, reservations, BUILT_AT).html)

    assert "09:00 to 10:00 1 h Jana Smyslena 2026-000001/RG-1-01 No experiment plan given" in text


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


def test_a_cancelled_reservation_is_crossed_out_marked_and_counted_apart() -> None:
    reservations = [
        reservation("2026-10-19T09:00", "2026-10-19T12:00", user_name="Petr Vymysleny"),
        reservation(
            "2026-10-20T13:00", "2026-10-20T15:00", user_name="Jana Smyslena", experiment_plan="", status=ReservationStatus.CANCELLED
        ),
    ]

    html = build_brief(INSTRUMENT, WEEK, reservations, BUILT_AT).html
    text = visible_text(html)

    assert "1 reservation " in text
    assert "3 h booked" in text
    assert "1 user " in text
    assert "0 without an experiment plan" in text
    assert "1 cancelled" in text
    assert "13:00 to 15:00 2 h Cancelled Jana Smyslena" in text
    row = html[html.index("13:00 to 15:00") - 300 : html.index("Jana Smyslena")]
    assert "line-through" in row


def test_a_requested_reservation_is_marked_and_still_counted() -> None:
    reservations = [
        reservation("2026-10-19T09:00", "2026-10-19T12:00", user_name="Jana Smyslena", status=ReservationStatus.REQUESTED)
    ]

    text = visible_text(build_brief(INSTRUMENT, WEEK, reservations, BUILT_AT).html)

    assert "09:00 to 12:00 3 h Requested Jana Smyslena" in text
    assert "1 reservation " in text
    assert "0 cancelled" in text


def test_a_reservation_that_ends_on_a_later_day_shows_its_end_day() -> None:
    reservations = [reservation("2026-10-19T15:00", "2026-10-20T13:00", user_name="Jana Smyslena")]

    text = visible_text(build_brief(INSTRUMENT, WEEK, reservations, BUILT_AT).html)

    assert "15:00 to Tue 13:00" in text


def test_a_reservation_that_ends_a_week_or_more_later_shows_its_end_date() -> None:
    reservations = [reservation("2026-10-19T15:00", "2026-10-27T13:00")]

    text = visible_text(build_brief(INSTRUMENT, WEEK, reservations, BUILT_AT).html)

    assert "15:00 to Tue 27. 10. 13:00" in text


@pytest.mark.parametrize(
    ("start", "end", "length"),
    [
        ("2026-10-19T09:00", "2026-10-19T09:15", "15 min"),
        ("2026-10-19T09:00", "2026-10-19T09:59", "59 min"),
        ("2026-10-19T09:00", "2026-10-19T10:00", "1 h"),
        ("2026-10-19T09:00", "2026-10-19T12:30", "3.5 h"),
        ("2026-10-19T15:00", "2026-10-20T13:00", "22 h"),
    ],
)
def test_each_reservation_shows_its_length(start: str, end: str, length: str) -> None:
    text = visible_text(build_brief(INSTRUMENT, WEEK, [reservation(start, end)], BUILT_AT).html)

    assert f"{end[-5:]} {length} Petr Vymysleny" in text


@pytest.mark.parametrize(
    ("experiment_plan", "note", "shown"),
    [
        ("SEM imaging", "Bring own sample holder", True),
        ("SEM imaging", "", False),
        ("SEM imaging", "  sem   IMAGING ", False),
        ("SEM imaging of thin films", "thin films", False),
        ("", "Thin films", True),
        # Only whole words count: "ok" is not inside "Imaging of books".
        ("Imaging of books", "ok", True),
    ],
)
def test_the_note_is_shown_only_when_it_adds_to_the_experiment_plan(
    experiment_plan: str, note: str, shown: bool
) -> None:
    reservations = [reservation("2026-10-19T09:00", "2026-10-19T10:00", experiment_plan=experiment_plan, note=note)]

    text = visible_text(build_brief(INSTRUMENT, WEEK, reservations, BUILT_AT).html)

    assert ("Note:" in text) is shown
    if shown:
        assert f"Note: {note}" in text


def test_the_private_note_is_shown_when_there_is_one() -> None:
    reservations = [
        reservation("2026-10-19T09:00", "2026-10-19T10:00", private_note="Paid by the group"),
        reservation("2026-10-20T09:00", "2026-10-20T10:00", private_note=" "),
    ]

    text = visible_text(build_brief(INSTRUMENT, WEEK, reservations, BUILT_AT).html)

    assert text.count("Private note:") == 1
    assert "Imaging of samples Private note: Paid by the group" in text


def test_empty_weekdays_are_shown_and_empty_weekend_days_are_left_out() -> None:
    reservations = [
        reservation("2026-10-20T09:00", "2026-10-20T10:00"),
        reservation("2026-10-25T09:00", "2026-10-25T10:00"),
    ]

    text = visible_text(build_brief(INSTRUMENT, WEEK, reservations, BUILT_AT).html)

    assert "Monday 19. 10. No reservations Tuesday 20. 10. 09:00" in text
    for weekday in ("Wednesday 21. 10.", "Thursday 22. 10.", "Friday 23. 10."):
        assert f"{weekday} No reservations" in text
    assert "Saturday" not in text
    assert "Sunday 25. 10. 09:00" in text


def test_a_week_without_reservations_says_so_in_a_short_brief() -> None:
    text = visible_text(build_brief(INSTRUMENT, WEEK, [], BUILT_AT).html)

    assert "No reservations this week." in text
    assert "Monday 19. 10. No reservations" not in text
    assert "without an experiment plan" not in text
    assert "Built on Monday 19. 10. 2026 at 08:00." in text


def test_a_week_with_only_cancelled_reservations_still_lists_them() -> None:
    reservations = [reservation("2026-10-20T09:00", "2026-10-20T10:00", status=ReservationStatus.CANCELLED)]

    text = visible_text(build_brief(INSTRUMENT, WEEK, reservations, BUILT_AT).html)

    assert "No reservations this week." not in text
    assert "1 cancelled" in text
    assert "09:00 to 10:00 1 h Cancelled" in text


def test_the_plain_text_version_shows_the_same_week() -> None:
    reservations = [
        reservation(
            "2026-10-20T15:00",
            "2026-10-21T13:00",
            user_name="Jana Smyslena",
            project_code="2026-123456/RG-1-01",
            experiment_plan="SEM imaging",
            note="Bring own holder",
            private_note="Paid by the group",
        ),
        reservation(
            "2026-10-20T09:00", "2026-10-20T09:30", user_name="Karel Neexistujici", experiment_plan="", status=ReservationStatus.REQUESTED
        ),
        reservation("2026-10-22T09:00", "2026-10-22T10:00", user_name="Petr Vymysleny", status=ReservationStatus.CANCELLED),
    ]

    text = build_brief(INSTRUMENT, WEEK, reservations, BUILT_AT).text

    assert text == """\
Weekly brief for Hana Testova
Test Electron Microscope
TEST-SEM - Monday 19. 10. to Sunday 25. 10. 2026

2 reservations, 22.5 h booked, 2 users, 1 without an experiment plan, 1 cancelled

MONDAY 19. 10.
  No reservations

TUESDAY 20. 10.
  [Requested] 09:00 to 09:30 (30 min)
    Karel Neexistujici, 2026-000001/RG-1-01
    No experiment plan given
  15:00 to Wed 13:00 (22 h)
    Jana Smyslena, 2026-123456/RG-1-01
    SEM imaging
    Note: Bring own holder
    Private note: Paid by the group

WEDNESDAY 21. 10.
  No reservations

THURSDAY 22. 10.
  [Cancelled] 09:00 to 10:00 (1 h)
    Petr Vymysleny, 2026-000001/RG-1-01
    Imaging of samples

FRIDAY 23. 10.
  No reservations

Built on Monday 19. 10. 2026 at 08:00.
Check the booking system for later changes.
"""


def test_the_plain_text_version_of_an_empty_week_says_so() -> None:
    text = build_brief(INSTRUMENT, WEEK, [], BUILT_AT).text

    assert "No reservations this week." in text
    assert "MONDAY" not in text
