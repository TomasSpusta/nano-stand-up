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
    BOOKED = "booked"
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


@dataclass(frozen=True)
class Week:
    """Monday 00:00 up to (not including) the next Monday 00:00."""

    monday: date

    def __post_init__(self) -> None:
        if self.monday.weekday() != 0:
            raise ValueError(f"Week must start on a Monday, got {self.monday:%A} {self.monday}")

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
