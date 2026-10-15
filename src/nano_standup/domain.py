"""Domain types shared by all modules. Words follow GLOSSARY.md."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

# The booking system and the guarantors work in Prague time.
PRAGUE = ZoneInfo("Europe/Prague")

# Gives the current time with a time zone. Passed in from outside, so tests can fix the time.
Clock = Callable[[], datetime]

# Day names written out, because `%A` and `%a` follow the server's time language.
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
# Saturday and Sunday, as date.weekday() gives them.
WEEKEND = {5, 6}


def day_name(day: date) -> str:
    """English day name, such as "Tuesday", on any server time language."""
    return DAY_NAMES[day.weekday()]


def normalize_email(email: str) -> str:
    """Emails match without regard to case or surrounding spaces."""
    return email.strip().lower()


@dataclass(frozen=True)
class Guarantor:
    name: str
    email: str


@dataclass(frozen=True)
class Instrument:
    id: str
    name: str
    alias: str
    guarantor: Guarantor

    @property
    def label(self) -> str:
        """Alias and id, for log and error messages."""
        return f"{self.alias} ({self.id})"


class ReservationStatus(Enum):
    RESERVED = "reserved"
    # Waiting for the guarantor to approve it, so it may not happen.
    REQUESTED = "requested"
    # The booking system keeps cancelled reservations.
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Reservation:
    """Times are naive Prague local time, as the booking system gives them."""

    id: str
    start: datetime
    end: datetime
    user_id: str
    user_name: str
    project_code: str
    experiment_plan: str
    status: ReservationStatus
    # The user's note on the reservation. Often it repeats the experiment plan.
    note: str
    # A note only staff can see.
    private_note: str

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    @property
    def is_not_cancelled(self) -> bool:
        """Reserved or requested. A requested reservation may not happen, but it still counts."""
        return self.status is not ReservationStatus.CANCELLED

    @property
    def is_missing_experiment_plan(self) -> bool:
        """Not cancelled and with no experiment plan. A cancelled reservation needs no experiment plan."""
        return self.is_not_cancelled and not self.experiment_plan.strip()


@dataclass(frozen=True)
class WorkingHours:
    """The working day, Monday to Friday. A reservation outside it may need an after-hours safety check."""

    start: time
    end: time

    def __post_init__(self) -> None:
        if self.start >= self.end:
            raise ValueError(f"Working day must start before it ends, got {self.start:%H:%M} to {self.end:%H:%M}")

    def covers(self, reservation: Reservation) -> bool:
        """True when the reservation starts and ends inside one working day.

        Times compare to the minute, as the brief shows them: an end at 19:00:30 shows as 19:00, and is inside.
        """
        start, end = reservation.start, reservation.end
        return (
            start.weekday() not in WEEKEND
            and end.date() == start.date()
            and _to_the_minute(start.time()) >= self.start
            and _to_the_minute(end.time()) <= self.end
        )


def _to_the_minute(moment: time) -> time:
    return moment.replace(second=0, microsecond=0)


@dataclass(frozen=True)
class Week:
    """Monday 00:00 up to (not including) the next Monday 00:00."""

    monday: date

    def __post_init__(self) -> None:
        if self.monday.weekday() != 0:
            raise ValueError(f"Week must start on a Monday, got {day_name(self.monday)} {self.monday}")

    @classmethod
    def containing(cls, day: date) -> "Week":
        return cls(day - timedelta(days=day.weekday()))

    @property
    def sunday(self) -> date:
        return self.monday + timedelta(days=6)

    @property
    def start(self) -> datetime:
        return datetime.combine(self.monday, time())

    @property
    def end(self) -> datetime:
        return self.start + timedelta(days=7)

    def days(self) -> list[date]:
        return [self.monday + timedelta(days=i) for i in range(7)]

    def overlaps(self, reservation: Reservation) -> bool:
        """True when some of the reservation is inside the week. One that ends exactly at Monday 00:00 is not."""
        return reservation.start < self.end and reservation.end > self.start

    def time_inside(self, reservation: Reservation) -> timedelta:
        """The part of the reservation inside the week, without any time before Monday or after Sunday."""
        return max(min(reservation.end, self.end) - max(reservation.start, self.start), timedelta())
