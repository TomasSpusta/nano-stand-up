from collections.abc import Mapping
from dataclasses import replace
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from nano_standup.brief import Brief, build_brief
from nano_standup.brief_content import brief_content
from nano_standup.domain import Certificate, Reservation, ReservationStatus, UserFacts, Week, WorkingHours
from nano_standup.heads_ups import HeadsUpSettings

from helpers import (
    HEADS_UP_SETTINGS,
    INSTRUMENT,
    experienced_users,
    heads_up_text,
    reservation,
    visible_text,
)

WEEK = Week(date(2026, 10, 19))
BUILT_AT = datetime(2026, 10, 19, 8, 0, tzinfo=ZoneInfo("Europe/Prague"))


def _build(
    reservations: list[Reservation],
    user_facts: Mapping[str, UserFacts] | None = None,
    week: Week = WEEK,
    built_at: datetime = BUILT_AT,
    settings: HeadsUpSettings = HEADS_UP_SETTINGS,
) -> Brief:
    """The brief for the test instrument. Without `user_facts`, every user is certified and not a new user."""
    facts = experienced_users(reservations) if user_facts is None else user_facts
    return build_brief(INSTRUMENT, week, reservations, facts, built_at, settings)


def test_subject_and_heading_name_the_instrument_and_the_week() -> None:
    brief = _build([])

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

    text = visible_text(_build(reservations).html)

    assert "3 reservations" in text
    assert "6.5 h booked" in text
    assert "2 users" in text
    assert "2 without an experiment plan" in text


def test_counts_use_singular_words_and_whole_hours() -> None:
    reservations = [reservation("2026-10-19T09:00", "2026-10-19T12:00")]

    text = visible_text(_build(reservations).html)

    assert "1 reservations" not in text
    assert "1 reservation " in text
    assert "3 h booked" in text
    assert "1 user " in text


def test_reservations_are_grouped_by_day_in_time_order() -> None:
    reservations = [
        reservation("2026-10-21T13:00", "2026-10-21T15:00", user_name="Jana Smyslena"),
        reservation("2026-10-19T14:00", "2026-10-19T16:00", user_name="Karel Neexistujici"),
        reservation(
            "2026-10-19T08:30", "2026-10-19T11:00", user_name="Petr Vymysleny", project_code="2026-123456/RG-1-01"
        ),
    ]

    text = visible_text(_build(reservations).html)

    monday = text.index("Monday 19. 10.", text.index("without an experiment plan"))
    first = text.index("08:30 to 11:00 2.5 h Petr Vymysleny 2026-123456/RG-1-01 Imaging of samples")
    second = text.index("14:00 to 16:00 2 h Karel Neexistujici")
    wednesday = text.index("Wednesday 21. 10.")
    third = text.index("13:00 to 15:00 2 h Jana Smyslena")
    assert monday < first < second < wednesday < third


def test_a_reservation_without_an_experiment_plan_says_so() -> None:
    reservations = [reservation("2026-10-19T09:00", "2026-10-19T10:00", user_name="Jana Smyslena", experiment_plan=" ")]

    text = visible_text(_build(reservations).html)

    assert "09:00 to 10:00 1 h Jana Smyslena 2026-000001/RG-1-01 No experiment plan given" in text


def test_footer_shows_the_build_time() -> None:
    late = datetime(2026, 10, 19, 9, 47, tzinfo=ZoneInfo("Europe/Prague"))

    text = visible_text(_build([], built_at=late).html)

    assert "Built on Monday 19. 10. 2026 at 09:47." in text
    assert "Check the booking system for later changes." in text


def test_user_text_is_escaped() -> None:
    reservations = [reservation("2026-10-19T09:00", "2026-10-19T10:00", experiment_plan="<b>bold</b> & more")]

    html = _build(reservations).html

    assert "&lt;b&gt;bold&lt;/b&gt; &amp; more" in html


def test_subject_spans_two_months_when_the_week_does() -> None:
    brief = _build([], week=Week(date(2026, 9, 28)))

    assert brief.subject == "Brief: TEST-SEM - week 28. 9.-4. 10. 2026"


def test_users_without_an_id_still_count_once_per_name() -> None:
    reservations = [
        reservation("2026-10-19T09:00", "2026-10-19T10:00", user_name="Jana Smyslena", user_id=""),
        reservation("2026-10-20T09:00", "2026-10-20T10:00", user_name="Karel Neexistujici", user_id=""),
    ]

    text = visible_text(_build(reservations).html)

    assert "2 users" in text


def test_a_build_time_without_a_time_zone_is_refused() -> None:
    with pytest.raises(ValueError, match="built_at must carry a time zone, got 2026-10-19T08:00:00"):
        _build([], built_at=datetime(2026, 10, 19, 8, 0))


def test_a_cancelled_reservation_is_crossed_out_marked_and_counted_apart() -> None:
    reservations = [
        reservation("2026-10-19T09:00", "2026-10-19T12:00", user_name="Petr Vymysleny"),
        reservation(
            "2026-10-20T13:00",
            "2026-10-20T15:00",
            user_name="Jana Smyslena",
            experiment_plan="",
            status=ReservationStatus.CANCELLED,
        ),
    ]

    html = _build(reservations).html
    text = visible_text(html)

    assert "1 reservation " in text
    assert "3 h booked" in text
    assert "1 user " in text
    assert "0 without an experiment plan" in text
    assert "1 cancelled" in text
    assert "13:00 to 15:00 2 h Cancelled Jana Smyslena" in text
    # The last match is the row: the Heads-up section above it names the same time and user.
    row = html[html.rindex("13:00 to 15:00") - 300 : html.rindex("Jana Smyslena")]
    assert "line-through" in row


def test_a_requested_reservation_is_marked_highlighted_and_still_counted() -> None:
    reservations = [
        reservation(
            "2026-10-19T09:00", "2026-10-19T12:00", user_name="Jana Smyslena", status=ReservationStatus.REQUESTED
        )
    ]

    html = _build(reservations).html
    text = visible_text(html)

    assert "09:00 to 12:00 3 h Requested: needs your approval Jana Smyslena" in text
    assert "1 reservation " in text
    assert "0 cancelled" in text
    # The last match is the row: the Heads-up section above it names the same time and user.
    row = html[html.rindex("09:00 to 12:00") - 300 : html.rindex("Jana Smyslena")]
    assert "background:#fffbeb" in row


def test_requested_reservations_are_counted_first_as_waiting_for_approval() -> None:
    reservations = [
        reservation("2026-10-19T09:00", "2026-10-19T10:00", status=ReservationStatus.REQUESTED),
        reservation("2026-10-20T09:00", "2026-10-20T10:00", status=ReservationStatus.REQUESTED),
        reservation("2026-10-21T09:00", "2026-10-21T10:00"),
    ]

    html = _build(reservations).html
    text = visible_text(html)

    assert text.index("2 waiting for your approval") < text.index("3 reservations")
    tile = html[html.index(">2</strong>") - 300 : html.index("waiting for your approval")]
    assert "background:#fffbeb" in tile


def test_no_waiting_count_without_requested_reservations() -> None:
    reservations = [reservation("2026-10-19T09:00", "2026-10-19T10:00")]

    brief = _build(reservations)

    assert "waiting for your approval" not in brief.html
    assert "waiting for your approval" not in brief.text


def test_a_reservation_that_ends_on_a_later_day_shows_its_end_day() -> None:
    reservations = [reservation("2026-10-19T15:00", "2026-10-20T13:00", user_name="Jana Smyslena")]

    text = visible_text(_build(reservations).html)

    assert "15:00 to Tue 13:00" in text


def test_a_reservation_that_ends_a_week_or_more_later_shows_its_end_date() -> None:
    reservations = [reservation("2026-10-19T15:00", "2026-10-27T13:00")]

    text = visible_text(_build(reservations).html)

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
    text = visible_text(_build([reservation(start, end)]).html)

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

    text = visible_text(_build(reservations).html)

    assert ("Note:" in text) is shown
    if shown:
        assert f"Note: {note}" in text


def test_the_private_note_is_shown_when_there_is_one() -> None:
    reservations = [
        reservation("2026-10-19T09:00", "2026-10-19T10:00", private_note="Paid by the group"),
        reservation("2026-10-20T09:00", "2026-10-20T10:00", private_note=" "),
    ]

    text = visible_text(_build(reservations).html)

    assert text.count("Private note:") == 1
    assert "Imaging of samples Private note: Paid by the group" in text


def test_empty_weekdays_are_shown_and_empty_weekend_days_are_left_out() -> None:
    reservations = [
        reservation("2026-10-20T09:00", "2026-10-20T10:00"),
        reservation("2026-10-25T09:00", "2026-10-25T10:00"),
    ]

    text = visible_text(_build(reservations).html)

    assert "Monday 19. 10. No reservations Tuesday 20. 10. 09:00" in text
    for weekday in ("Wednesday 21. 10.", "Thursday 22. 10.", "Friday 23. 10."):
        assert f"{weekday} No reservations" in text
    assert "Saturday" not in text
    assert "Sunday 25. 10. 09:00" in text


def test_a_week_without_reservations_says_so_in_a_short_brief() -> None:
    text = visible_text(_build([]).html)

    assert "No reservations this week." in text
    assert "Monday 19. 10. No reservations" not in text
    assert "without an experiment plan" not in text
    assert "Built on Monday 19. 10. 2026 at 08:00." in text


def test_a_week_with_only_cancelled_reservations_still_lists_them() -> None:
    reservations = [reservation("2026-10-20T09:00", "2026-10-20T10:00", status=ReservationStatus.CANCELLED)]

    text = visible_text(_build(reservations).html)

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
            "2026-10-20T09:00",
            "2026-10-20T09:30",
            user_name="Karel Neexistujici",
            experiment_plan="",
            status=ReservationStatus.REQUESTED,
        ),
        reservation(
            "2026-10-22T09:00", "2026-10-22T10:00", user_name="Petr Vymysleny", status=ReservationStatus.CANCELLED
        ),
    ]

    text = _build(reservations).text

    expected = """\
Weekly brief for Hana Testova
Test Electron Microscope
TEST-SEM - Monday 19. 10. to Sunday 25. 10. 2026

HEADS-UP
  Waiting for your approval
    Tue 20. 10. 09:00 to 09:30, Karel Neexistujici
  No experiment plan
    Tue 20. 10. 09:00 to 09:30, Karel Neexistujici
  Outside working hours (07:00 to 19:00, Monday to Friday)
    Tue 20. 10. 15:00 to Wed 13:00, Jana Smyslena
  Cancelled
    Thu 22. 10. 09:00 to 10:00, Petr Vymysleny

1 waiting for your approval, 2 reservations, 22.5 h booked, 2 users, 1 without an experiment plan, 1 cancelled

MONDAY 19. 10.
  No reservations

TUESDAY 20. 10.
  [Requested: needs your approval] 09:00 to 09:30 (30 min)
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
    assert text == expected


def test_the_plain_text_version_of_an_empty_week_says_so() -> None:
    text = _build([]).text

    assert "No reservations this week." in text
    assert "MONDAY" not in text


def test_a_reservation_that_runs_in_from_the_previous_week_shows_on_monday_with_its_start_day() -> None:
    reservations = [reservation("2026-10-18T22:00", "2026-10-19T06:00", user_name="Jana Smyslena")]

    brief = _build(reservations)
    text = visible_text(brief.html)

    assert "Monday 19. 10. from Sun 22:00 to 06:00 8 h Jana Smyslena" in text
    assert "MONDAY 19. 10.\n  from Sun 22:00 to 06:00 (8 h)\n    Jana Smyslena" in brief.text


def test_a_reservation_that_runs_in_from_the_previous_week_counts_only_its_hours_inside_the_week() -> None:
    reservations = [reservation("2026-10-18T22:00", "2026-10-19T06:00", user_name="Jana Smyslena")]

    text = visible_text(_build(reservations).html)

    assert "1 reservation " in text
    assert "6 h booked" in text
    assert "1 user " in text


def test_a_reservation_that_runs_through_the_whole_week_counts_only_the_hours_inside_it() -> None:
    reservations = [reservation("2026-10-18T20:00", "2026-10-26T02:00")]

    brief = _build(reservations)
    text = visible_text(brief.html)

    assert "168 h booked" in text
    assert "from Sun 20:00 to Mon 26. 10. 02:00 174 h" in text


def test_a_reservation_that_runs_past_sunday_counts_only_its_hours_inside_the_week() -> None:
    reservations = [reservation("2026-10-25T20:00", "2026-10-26T02:00")]

    text = visible_text(_build(reservations).html)

    assert "4 h booked" in text
    assert "20:00 to Mon 02:00 6 h" in text


def test_a_reservation_that_started_a_week_or_more_before_shows_its_start_date() -> None:
    reservations = [reservation("2026-10-12T09:00", "2026-10-19T10:00")]

    text = visible_text(_build(reservations).html)

    assert "Monday 19. 10. from Mon 12. 10. 09:00 to 10:00" in text


def test_a_cancelled_reservation_that_runs_in_shows_on_monday_and_counts_as_cancelled() -> None:
    reservations = [reservation("2026-10-18T22:00", "2026-10-19T06:00", status=ReservationStatus.CANCELLED)]

    text = visible_text(_build(reservations).html)

    assert "from Sun 22:00 to 06:00 8 h Cancelled" in text
    assert "0 h booked" in text
    assert "1 cancelled" in text


def test_a_requested_reservation_that_runs_in_is_waiting_for_approval() -> None:
    reservations = [reservation("2026-10-18T22:00", "2026-10-19T06:00", status=ReservationStatus.REQUESTED)]

    text = visible_text(_build(reservations).html)

    assert "1 waiting for your approval" in text
    assert "from Sun 22:00 to 06:00 8 h Requested: needs your approval" in text
    assert "6 h booked" in text


def test_the_html_version_keeps_the_start_day_and_time_together() -> None:
    reservations = [reservation("2026-10-18T22:00", "2026-10-19T06:00")]

    html = _build(reservations).html

    assert "from Sun&nbsp;22:00 to 06:00" in html


def test_a_reservation_that_started_in_an_earlier_year_shows_the_year() -> None:
    reservations = [reservation("2024-01-04T09:00", "2026-10-19T10:00")]

    text = visible_text(_build(reservations).html)

    assert "Monday 19. 10. from Thu 4. 1. 2024 09:00 to 10:00" in text


def _heads_up_text(brief: Brief) -> str:
    return heads_up_text(brief.text, brief.html)


def test_a_reservation_without_an_experiment_plan_is_a_heads_up() -> None:
    reservations = [
        reservation("2026-10-19T09:00", "2026-10-19T10:00", user_name="Petr Vymysleny"),
        reservation("2026-10-20T13:00", "2026-10-20T14:30", user_name="Jana Smyslena", experiment_plan=" "),
    ]

    heads_up = _heads_up_text(_build(reservations))

    assert "No experiment plan Tue 20. 10. 13:00 to 14:30, Jana Smyslena" in heads_up
    assert "Petr Vymysleny" not in heads_up


def test_the_heads_up_section_is_left_out_when_no_rule_matches() -> None:
    reservations = [reservation("2026-10-19T07:00", "2026-10-19T19:00")]

    brief = _build(reservations)

    assert "Heads-up" not in brief.html
    assert "HEADS-UP" not in brief.text


@pytest.mark.parametrize(
    ("start", "end", "item"),
    [
        ("2026-10-19T06:59", "2026-10-19T10:00", "Mon 19. 10. 06:59 to 10:00, Jana Smyslena"),
        ("2026-10-19T17:00", "2026-10-19T19:01", "Mon 19. 10. 17:00 to 19:01, Jana Smyslena"),
        # Times compare to the minute, as the brief shows them.
        ("2026-10-19T06:59:30", "2026-10-19T10:00", "Mon 19. 10. 06:59 to 10:00, Jana Smyslena"),
        # Overnight.
        ("2026-10-20T22:00", "2026-10-21T02:00", "Tue 20. 10. 22:00 to Wed 02:00, Jana Smyslena"),
        ("2026-10-20T17:00", "2026-10-21T00:00", "Tue 20. 10. 17:00 to Wed 00:00, Jana Smyslena"),
        ("2026-10-23T18:00", "2026-10-24T01:00", "Fri 23. 10. 18:00 to Sat 01:00, Jana Smyslena"),
        # Early start and overnight.
        ("2026-10-19T06:00", "2026-10-20T02:00", "Mon 19. 10. 06:00 to Tue 02:00, Jana Smyslena"),
        # Weekend.
        ("2026-10-24T10:00", "2026-10-24T12:00", "Sat 24. 10. 10:00 to 12:00, Jana Smyslena"),
        ("2026-10-25T10:00", "2026-10-25T12:00", "Sun 25. 10. 10:00 to 12:00, Jana Smyslena"),
        # Runs in from the previous week: it shows on Monday, as in the list.
        ("2026-10-18T22:00", "2026-10-19T06:00", "Mon 19. 10. from Sun 22:00 to 06:00, Jana Smyslena"),
    ],
)
def test_a_reservation_outside_working_hours_is_a_heads_up(start: str, end: str, item: str) -> None:
    reservations = [reservation(start, end, user_name="Jana Smyslena")]

    heads_up = _heads_up_text(_build(reservations))

    assert f"Outside working hours (07:00 to 19:00, Monday to Friday) {item}" in heads_up


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2026-10-19T07:00", "2026-10-19T19:00"),
        ("2026-10-19T07:00", "2026-10-19T08:00"),
        ("2026-10-19T18:00", "2026-10-19T19:00"),
        # Shows as 19:00.
        ("2026-10-19T18:00", "2026-10-19T19:00:30"),
        ("2026-10-23T10:00", "2026-10-23T12:00"),
    ],
)
def test_a_reservation_inside_working_hours_is_not_a_heads_up(start: str, end: str) -> None:
    brief = _build([reservation(start, end)])

    assert "Outside working hours" not in brief.html
    assert "Outside working hours" not in brief.text


def test_a_cancelled_reservation_needs_no_experiment_plan_and_no_after_hours_check() -> None:
    reservations = [
        reservation("2026-10-24T22:00", "2026-10-25T02:00", experiment_plan="", status=ReservationStatus.CANCELLED)
    ]

    heads_up = _heads_up_text(_build(reservations))

    assert "No experiment plan" not in heads_up
    assert "Outside working hours" not in heads_up


def test_cancelled_and_requested_reservations_are_heads_ups() -> None:
    reservations = [
        reservation("2026-10-19T09:00", "2026-10-19T10:00", user_name="Petr Vymysleny"),
        reservation(
            "2026-10-20T09:00", "2026-10-20T10:00", user_name="Jana Smyslena", status=ReservationStatus.CANCELLED
        ),
        reservation(
            "2026-10-21T09:00", "2026-10-21T10:00", user_name="Karel Neexistujici", status=ReservationStatus.REQUESTED
        ),
    ]

    heads_up = _heads_up_text(_build(reservations))

    assert "Cancelled Tue 20. 10. 09:00 to 10:00, Jana Smyslena" in heads_up
    assert "Waiting for your approval Wed 21. 10. 09:00 to 10:00, Karel Neexistujici" in heads_up
    assert "Petr Vymysleny" not in heads_up


def test_reservations_that_are_neither_cancelled_nor_requested_are_not_heads_ups() -> None:
    reservations = [reservation("2026-10-19T09:00", "2026-10-19T10:00", experiment_plan="")]

    heads_up = _heads_up_text(_build(reservations))

    assert "Cancelled" not in heads_up
    assert "Waiting for your approval" not in heads_up


def test_the_heads_ups_come_in_a_fixed_order_waiting_for_approval_first_and_cancelled_last() -> None:
    # Given in the opposite order, so the order comes from the rules and not from the reservations.
    reservations = [
        reservation("2026-10-19T09:00", "2026-10-19T10:00", status=ReservationStatus.CANCELLED),
        reservation("2026-10-20T06:00", "2026-10-20T08:00"),
        reservation("2026-10-21T09:00", "2026-10-21T10:00", experiment_plan=""),
        reservation("2026-10-22T09:00", "2026-10-22T10:00", status=ReservationStatus.REQUESTED),
    ]

    content = brief_content(
        INSTRUMENT, WEEK, reservations, experienced_users(reservations), BUILT_AT, HEADS_UP_SETTINGS
    )

    assert [heads_up.title for heads_up in content.heads_ups] == [
        "Waiting for your approval",
        "No experiment plan",
        "Outside working hours (07:00 to 19:00, Monday to Friday)",
        "Cancelled",
    ]


def test_the_heads_ups_come_first_above_the_counts() -> None:
    reservations = [
        reservation("2026-10-19T09:00", "2026-10-19T10:00", user_name="Petr Vymysleny", experiment_plan=""),
        reservation("2026-10-20T06:00", "2026-10-20T08:00", user_name="Jana Smyslena", experiment_plan=""),
    ]

    brief = _build(reservations)

    expected = """\
TEST-SEM - Monday 19. 10. to Sunday 25. 10. 2026

HEADS-UP
  No experiment plan
    Mon 19. 10. 09:00 to 10:00, Petr Vymysleny
    Tue 20. 10. 06:00 to 08:00, Jana Smyslena
  Outside working hours (07:00 to 19:00, Monday to Friday)
    Tue 20. 10. 06:00 to 08:00, Jana Smyslena

2 reservations, 3 h booked, 2 users, 2 without an experiment plan, 0 cancelled

MONDAY 19. 10.
"""
    assert expected in brief.text
    assert "TEST-SEM - Monday 19. 10. to Sunday 25. 10. 2026 Heads-up No experiment plan" in visible_text(brief.html)


def test_the_working_hours_come_from_the_settings() -> None:
    reservations = [
        reservation("2026-10-19T07:30", "2026-10-19T16:00", user_name="Petr Vymysleny"),
        reservation("2026-10-20T08:00", "2026-10-20T17:00", user_name="Jana Smyslena"),
    ]

    brief = _build(
        reservations, settings=replace(HEADS_UP_SETTINGS, working_hours=WorkingHours(time(8, 0), time(17, 0)))
    )

    assert _heads_up_text(brief) == (
        "Outside working hours (08:00 to 17:00, Monday to Friday) Mon 19. 10. 07:30 to 16:00, Petr Vymysleny"
    )


def _facts(certificates: list[Certificate], *starts: str) -> UserFacts:
    return UserFacts(certificates, [datetime.fromisoformat(start) for start in starts])


CERTIFIED_IN_OCTOBER = Certificate(valid_from=date(2026, 10, 1), valid_to=date(2027, 10, 1))


def test_a_new_user_is_a_heads_up_with_the_reservation_number_and_a_row_mark() -> None:
    jana = reservation("2026-10-20T09:00", "2026-10-20T10:00", user_name="Jana Smyslena")
    facts = {jana.user_id: _facts([CERTIFIED_IN_OCTOBER], "2026-10-05T09:00", "2026-10-20T09:00")}

    brief = _build([jana], facts)

    assert _heads_up_text(brief) == "New user Tue 20. 10. 09:00 to 10:00, Jana Smyslena (no. 2)"
    assert "09:00 to 10:00 1 h New user, no. 2 Jana Smyslena" in visible_text(brief.html)
    assert "  [New user, no. 2] 09:00 to 10:00 (1 h)\n    Jana Smyslena" in brief.text


def test_a_user_without_a_valid_certificate_is_a_heads_up_with_the_reason_and_a_row_mark() -> None:
    jana = reservation("2026-10-20T09:00", "2026-10-20T10:00", user_name="Jana Smyslena")
    karel = reservation("2026-10-21T09:00", "2026-10-21T10:00", user_name="Karel Neexistujici")
    expired = [
        Certificate(valid_from=date(2024, 1, 1), valid_to=date(2025, 12, 31)),
        Certificate(valid_from=date(2025, 1, 1), valid_to=date(2026, 5, 31)),
    ]
    facts = {
        jana.user_id: _facts([]),
        karel.user_id: _facts(expired, "2025-02-02T09:00", "2025-03-02T09:00", "2025-04-02T09:00"),
    }

    brief = _build([jana, karel], facts)

    assert _heads_up_text(brief) == (
        "No valid certificate Tue 20. 10. 09:00 to 10:00, Jana Smyslena (missing) "
        "Wed 21. 10. 09:00 to 10:00, Karel Neexistujici (expired 31. 5. 2026)"
    )
    assert "09:00 to 10:00 1 h No valid certificate Karel Neexistujici" in visible_text(brief.html)
    assert "  [No valid certificate] 09:00 to 10:00 (1 h)\n    Karel Neexistujici" in brief.text


def test_a_certificate_that_starts_after_the_reservation_says_when_it_starts() -> None:
    jana = reservation("2026-10-20T09:00", "2026-10-20T10:00", user_name="Jana Smyslena")
    later = Certificate(valid_from=date(2026, 11, 2), valid_to=None)

    brief = _build([jana], {jana.user_id: _facts([later])})

    assert _heads_up_text(brief) == (
        "No valid certificate Tue 20. 10. 09:00 to 10:00, Jana Smyslena (valid from 2. 11. 2026)"
    )


def test_a_user_the_brief_has_no_facts_about_has_no_certificate() -> None:
    brief = _build([reservation("2026-10-20T09:00", "2026-10-20T10:00", user_name="Jana Smyslena")], {})

    assert _heads_up_text(brief) == "No valid certificate Tue 20. 10. 09:00 to 10:00, Jana Smyslena (missing)"


def test_a_user_whose_certificate_has_just_expired_can_be_a_new_user_too() -> None:
    jana = reservation("2026-10-20T09:00", "2026-10-20T10:00", user_name="Jana Smyslena")
    ended_yesterday = Certificate(valid_from=date(2026, 10, 1), valid_to=date(2026, 10, 19))

    brief = _build([jana], {jana.user_id: _facts([ended_yesterday])})

    assert _heads_up_text(brief) == (
        "New user Tue 20. 10. 09:00 to 10:00, Jana Smyslena (no. 1) "
        "No valid certificate Tue 20. 10. 09:00 to 10:00, Jana Smyslena (expired 19. 10. 2026)"
    )
    assert "09:00 to 10:00 1 h New user, no. 1 No valid certificate Jana Smyslena" in visible_text(brief.html)
    assert "  [New user, no. 1] [No valid certificate] 09:00 to 10:00 (1 h)" in brief.text


def test_a_requested_reservation_shows_its_status_mark_first_then_the_user_marks() -> None:
    jana = reservation(
        "2026-10-20T09:00", "2026-10-20T10:00", user_name="Jana Smyslena", status=ReservationStatus.REQUESTED
    )

    brief = _build([jana], {jana.user_id: _facts([CERTIFIED_IN_OCTOBER])})

    assert "  [Requested: needs your approval] [New user, no. 1] 09:00 to 10:00 (1 h)" in brief.text
    assert "Requested: needs your approval New user, no. 1 Jana Smyslena" in visible_text(brief.html)


def test_a_cancelled_reservation_is_never_by_a_new_user_or_without_a_certificate() -> None:
    reservations = [
        reservation(
            "2026-10-20T09:00", "2026-10-20T10:00", user_name="Jana Smyslena", status=ReservationStatus.CANCELLED
        ),
        reservation(
            "2026-10-21T09:00", "2026-10-21T10:00", user_name="Karel Neexistujici", status=ReservationStatus.CANCELLED
        ),
    ]
    facts = {reservations[0].user_id: _facts([CERTIFIED_IN_OCTOBER]), reservations[1].user_id: _facts([])}

    brief = _build(reservations, facts)

    assert _heads_up_text(brief).startswith("Cancelled ")
    assert "New user" not in brief.text
    assert "No valid certificate" not in brief.text


def test_the_new_user_limit_comes_from_the_settings() -> None:
    jana = reservation("2026-10-20T09:00", "2026-10-20T10:00", user_name="Jana Smyslena")
    facts = {jana.user_id: _facts([CERTIFIED_IN_OCTOBER], "2026-10-05T09:00")}

    brief = _build([jana], facts, settings=replace(HEADS_UP_SETTINGS, new_user_reservations=1))

    assert "New user" not in brief.text


def test_the_rules_about_users_come_after_working_hours_and_before_cancelled() -> None:
    reservations = [
        reservation("2026-10-19T09:00", "2026-10-19T10:00", user_name="Jana Smyslena"),
        reservation("2026-10-20T06:00", "2026-10-20T08:00", user_name="Petr Vymysleny"),
        reservation(
            "2026-10-21T09:00", "2026-10-21T10:00", user_name="Petr Vymysleny", status=ReservationStatus.CANCELLED
        ),
    ]
    # Jana is a new user whose certificate has expired.
    ended = Certificate(valid_from=date(2026, 10, 1), valid_to=date(2026, 10, 18))
    facts = {**experienced_users(reservations), reservations[0].user_id: _facts([ended])}

    content = brief_content(INSTRUMENT, WEEK, reservations, facts, BUILT_AT, HEADS_UP_SETTINGS)

    assert [heads_up.title for heads_up in content.heads_ups] == [
        "Outside working hours (07:00 to 19:00, Monday to Friday)",
        "New user",
        "No valid certificate",
        "Cancelled",
    ]


def test_a_guarantor_without_a_user_id_does_not_hide_a_user_without_one() -> None:
    no_user_id = replace(INSTRUMENT, guarantor=replace(INSTRUMENT.guarantor, user_id=None))
    reservations = [reservation("2026-10-20T09:00", "2026-10-20T10:00", user_name="Jana Smyslena", user_id="")]

    brief = build_brief(no_user_id, WEEK, reservations, {}, BUILT_AT, HEADS_UP_SETTINGS)

    assert _heads_up_text(brief) == "No valid certificate Tue 20. 10. 09:00 to 10:00, Jana Smyslena (missing)"
