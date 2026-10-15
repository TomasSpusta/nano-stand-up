"""What a brief shows, worked out once from an instrument's week. The HTML and plain-text versions both draw it."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from nano_standup.domain import (
    PRAGUE,
    WEEKEND,
    Instrument,
    Reservation,
    ReservationStatus,
    Week,
    WorkingHours,
    day_name,
)
from nano_standup.heads_ups import WAITING_FOR_APPROVAL, heads_up_rules

NO_EXPERIMENT_PLAN = "No experiment plan given"
NO_RESERVATIONS_THIS_WEEK = "No reservations this week."
NO_RESERVATIONS_THIS_DAY = "No reservations"
FOOTER_REMINDER = "Check the booking system for later changes."
MARKS = {
    ReservationStatus.CANCELLED: "Cancelled",
    ReservationStatus.REQUESTED: "Requested: needs your approval",
}


@dataclass(frozen=True)
class Count:
    value: str
    label: str
    # The guarantor has to act on it, so the brief makes it stand out.
    needs_action: bool = False


@dataclass(frozen=True)
class LabelledNote:
    label: str
    text: str


@dataclass(frozen=True)
class TimeSpan:
    """When a reservation runs, as seen from the day it shows on."""

    # The start time, with the start day when the reservation runs in from the previous week: "Sun 22:00".
    start: str
    # The end time, with the end day when it is on a later day: "Tue 13:00".
    end: str
    # Shown on Monday, though it started in the previous week.
    runs_in: bool

    def text(self, keep_together: Callable[[str], str] = lambda part: part) -> str:
        """The span as "15:00 to Tue 13:00".

        It is "from Sun 22:00 to 06:00" when the reservation runs in from the previous week.

        `keep_together` wraps the start and the end, so the HTML version can keep each day and time on one line.
        """
        return f"{'from ' if self.runs_in else ''}{keep_together(self.start)} to {keep_together(self.end)}"


@dataclass(frozen=True)
class Row:
    """One reservation as the brief shows it. Empty strings are left out."""

    status: ReservationStatus
    span: TimeSpan
    length: str
    user_name: str
    project_code: str
    experiment_plan: str
    note: str
    private_note: str

    @property
    def mark(self) -> str:
        return MARKS.get(self.status, "")

    @property
    def notes(self) -> list[LabelledNote]:
        """The note and the private note, in that order, even when empty.

        Each version of the brief leaves out an empty one.
        """
        return [LabelledNote("Note", self.note), LabelledNote("Private note", self.private_note)]


@dataclass(frozen=True)
class HeadsUpItem:
    """One matching reservation: the day it shows on, its time, and its user, as in its row."""

    # "Tue 20. 10."
    day: str
    span: TimeSpan
    user_name: str


@dataclass(frozen=True)
class HeadsUp:
    """One heads-up rule that matched, with its reservations."""

    title: str
    items: list[HeadsUpItem]


@dataclass(frozen=True)
class Day:
    calendar_day: date
    rows: list[Row]

    @property
    def heading(self) -> str:
        return f"{day_name(self.calendar_day)} {_day_month(self.calendar_day)}"


@dataclass(frozen=True)
class BriefContent:
    subject: str
    for_whom: str
    instrument_name: str
    week_line: str
    # Empty for a week without reservations: it gets a short brief with no counts and no days.
    counts: list[Count]
    # Empty when no rule matches: the brief then leaves out the Heads-up section.
    heads_ups: list[HeadsUp]
    days: list[Day]
    built: str

    @property
    def has_reservations(self) -> bool:
        return bool(self.days)


def brief_content(
    instrument: Instrument,
    week: Week,
    reservations: list[Reservation],
    built_at: datetime,
    working_hours: WorkingHours,
) -> BriefContent:
    """`built_at` must carry a time zone; the footer shows it in Prague time."""
    if built_at.tzinfo is None:
        raise ValueError(f"built_at must carry a time zone, got {built_at.isoformat()}")
    reservations = sorted(reservations, key=lambda r: r.start)
    built_local = built_at.astimezone(PRAGUE)
    return BriefContent(
        subject=f"Brief: {instrument.alias} - week {_week_span(week)}",
        for_whom=f"Weekly brief for {instrument.guarantor.name}",
        instrument_name=instrument.name,
        week_line=f"{instrument.alias} - Monday {_day_month(week.monday)} to Sunday {_day_month_year(week.sunday)}",
        counts=_counts(week, reservations) if reservations else [],
        heads_ups=_heads_ups(week, reservations, working_hours),
        days=_days(week, reservations) if reservations else [],
        built=f"Built on {_full_date(built_local.date())} at {built_local:%H:%M}.",
    )


def _counts(week: Week, reservations: list[Reservation]) -> list[Count]:
    """Cancelled reservations count only as cancelled. Requested ones count as reservations too.

    Hours count only inside the week, for a reservation that runs in from the previous week or past Sunday.
    """
    active = [r for r in reservations if r.is_not_cancelled]
    cancelled = len(reservations) - len(active)
    waiting = sum(1 for r in reservations if WAITING_FOR_APPROVAL.matches(r))
    hours = sum(week.time_inside(r).total_seconds() for r in active) / 3600
    # Reservations with no user id still count each user name once.
    users = len({r.user_id or r.user_name for r in active})
    without_experiment_plan = sum(1 for r in reservations if r.is_missing_experiment_plan)
    # Shown only when there is something to approve, and first, because it asks the guarantor to act.
    waiting_count = [Count(str(waiting), WAITING_FOR_APPROVAL.title.lower(), needs_action=True)] if waiting else []
    return waiting_count + [
        Count(str(len(active)), _plural(len(active), "reservation")),
        Count(f"{_format_hours(hours)} h", "booked"),
        Count(str(users), _plural(users, "user")),
        Count(str(without_experiment_plan), "without an experiment plan"),
        Count(str(cancelled), "cancelled"),
    ]


def _heads_ups(week: Week, reservations: list[Reservation], working_hours: WorkingHours) -> list[HeadsUp]:
    """The rules that match, in a fixed order, each with its reservations in the order given."""
    heads_ups = [
        HeadsUp(rule.title, [_heads_up_item(r, week) for r in reservations if rule.matches(r)])
        for rule in heads_up_rules(working_hours)
    ]
    return [heads_up for heads_up in heads_ups if heads_up.items]


def _heads_up_item(reservation: Reservation, week: Week) -> HeadsUpItem:
    day = _shown_on(reservation, week)
    return HeadsUpItem(f"{_short_day_name(day)} {_day_month(day)}", _span(reservation, day), reservation.user_name)


def _shown_on(reservation: Reservation, week: Week) -> date:
    """Its start day, or Monday when it runs in from the previous week."""
    return max(reservation.start.date(), week.monday)


def _days(week: Week, reservations: list[Reservation]) -> list[Day]:
    """Every weekday, empty or not. Weekend days only when they have reservations."""
    days = []
    for day in week.days():
        rows = [_row(r, day) for r in reservations if _shown_on(r, week) == day]
        if rows or day.weekday() not in WEEKEND:
            days.append(Day(day, rows))
    return days


def _row(reservation: Reservation, day: date) -> Row:
    """`day` is the day the row shows on."""
    note = reservation.note.strip()
    return Row(
        status=reservation.status,
        span=_span(reservation, day),
        length=_length(reservation),
        user_name=reservation.user_name,
        project_code=reservation.project_code,
        experiment_plan=reservation.experiment_plan.strip(),
        note=note if _adds_to(note, reservation.experiment_plan) else "",
        private_note=reservation.private_note.strip(),
    )


def _span(reservation: Reservation, day: date) -> TimeSpan:
    """`day` is the day the reservation shows on."""
    return TimeSpan(
        start=_time_seen_from(reservation.start, day),
        end=_time_seen_from(reservation.end, day),
        runs_in=reservation.start.date() < day,
    )


def _adds_to(note: str, experiment_plan: str) -> bool:
    """Users often copy the experiment plan into the note. The note adds something only when it holds more.

    Whole words only: the note "ok" adds to the experiment plan "Imaging of books".
    """
    note = _comparable(note)
    return bool(note) and f" {note} " not in f" {_comparable(experiment_plan)} "


def _comparable(text: str) -> str:
    return " ".join(text.casefold().split())


def _time_seen_from(moment: datetime, day: date) -> str:
    """The time as "11:00" on `day`.

    On another day it shows the day, "Tue 13:00", and the date too when a week or more away.

    The date has the year when it is not the year of `day`: "Thu 4. 1. 2024 09:00".
    """
    if moment.date() == day:
        return f"{moment:%H:%M}"
    if abs(moment.date() - day) < timedelta(days=7):
        return f"{_short_day_name(moment.date())} {moment:%H:%M}"
    year = f" {moment.year}" if moment.year != day.year else ""
    return f"{_short_day_name(moment.date())} {_day_month(moment.date())}{year} {moment:%H:%M}"


def _length(reservation: Reservation) -> str:
    """Under an hour in minutes, "15 min"; longer in hours, "3.5 h"."""
    minutes = round(reservation.duration.total_seconds() / 60)
    if minutes < 60:
        return f"{minutes} min"
    return f"{_format_hours(minutes / 60)} h"


def _plural(count: int, word: str) -> str:
    return word if count == 1 else f"{word}s"


def _format_hours(value: float) -> str:
    """One decimal place, dropped when the value is whole: "6.5", "3"."""
    return f"{value:.1f}".removesuffix(".0")


def _week_span(week: Week) -> str:
    """The week as "19.-25. 10. 2026", or "28. 9.-4. 10. 2026" when the week spans two months."""
    monday, sunday = week.monday, week.sunday
    if monday.year != sunday.year:
        return f"{_day_month_year(monday)}-{_day_month_year(sunday)}"
    if monday.month != sunday.month:
        return f"{_day_month(monday)}-{_day_month_year(sunday)}"
    return f"{monday.day}.-{_day_month_year(sunday)}"


def _short_day_name(day: date) -> str:
    """Short day name, such as "Tue"."""
    return day_name(day)[:3]


def _day_month(day: date) -> str:
    return f"{day.day}. {day.month}."


def _day_month_year(day: date) -> str:
    """A day with its year, such as "20. 10. 2026"."""
    return f"{_day_month(day)} {day.year}"


def _full_date(day: date) -> str:
    """A day with its name and year, such as "Tuesday 20. 10. 2026"."""
    return f"{day_name(day)} {_day_month_year(day)}"
