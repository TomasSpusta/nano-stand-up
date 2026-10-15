"""Heads-up rules: fixed rules that pick the reservations of the week a guarantor may want to act on.

Each rule gives its matching reservations, or none. The brief shows only the rules with a match.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import time

from nano_standup.domain import WEEKEND, Reservation, ReservationStatus

# Working hours, Monday to Friday. A reservation outside them may need an after-hours safety check.
WORKING_DAY_START = time(7, 0)
WORKING_DAY_END = time(19, 0)


@dataclass(frozen=True)
class HeadsUpRule:
    title: str
    matches: Callable[[Reservation], bool]


@dataclass(frozen=True)
class HeadsUpMatch:
    title: str
    reservations: list[Reservation]


def _outside_working_hours(reservation: Reservation) -> bool:
    """Starts before the working day, ends after it or on a later day, or starts on a weekend.

    A cancelled reservation will not happen, so it needs no safety check.
    """
    start, end = reservation.start, reservation.end
    return reservation.status is not ReservationStatus.CANCELLED and (
        start.time() < WORKING_DAY_START
        or end.time() > WORKING_DAY_END
        or end.date() > start.date()
        or start.weekday() in WEEKEND
    )


# First the reservations the guarantor has to approve, as in the counts.
RULES = [
    HeadsUpRule("Waiting for your approval", lambda reservation: reservation.status is ReservationStatus.REQUESTED),
    HeadsUpRule("No experiment plan", lambda reservation: reservation.lacks_experiment_plan),
    HeadsUpRule(
        f"Outside working hours ({WORKING_DAY_START:%H:%M} to {WORKING_DAY_END:%H:%M}, Monday to Friday)",
        _outside_working_hours,
    ),
    # The guarantor can re-plan a freed slot.
    HeadsUpRule("Cancelled", lambda reservation: reservation.status is ReservationStatus.CANCELLED),
]


def heads_ups(reservations: list[Reservation]) -> list[HeadsUpMatch]:
    """The rules that match, in a fixed order, each with its reservations in the order given."""
    matches = [HeadsUpMatch(rule.title, [r for r in reservations if rule.matches(r)]) for rule in RULES]
    return [match for match in matches if match.reservations]
