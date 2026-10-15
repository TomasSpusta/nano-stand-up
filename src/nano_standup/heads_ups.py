"""Heads-up rules: fixed rules that pick the reservations of the week a guarantor may want to act on.

Each rule gives its matching reservations, or none. The brief shows only the rules with a match.
"""

from collections.abc import Callable
from dataclasses import dataclass

from nano_standup.domain import Reservation, ReservationStatus, WorkingHours


@dataclass(frozen=True)
class HeadsUpRule:
    title: str
    matches: Callable[[Reservation], bool]


@dataclass(frozen=True)
class HeadsUpMatch:
    """A rule that matched, with its reservations. The brief turns it into a heads-up it can show."""

    title: str
    reservations: list[Reservation]


def _rules(working_hours: WorkingHours) -> list[HeadsUpRule]:
    # First the reservations the guarantor has to approve, as in the counts.
    return [
        HeadsUpRule("Waiting for your approval", lambda reservation: reservation.status is ReservationStatus.REQUESTED),
        HeadsUpRule("No experiment plan", lambda reservation: reservation.lacks_experiment_plan),
        HeadsUpRule(
            f"Outside working hours ({working_hours.start:%H:%M} to {working_hours.end:%H:%M}, Monday to Friday)",
            # A cancelled reservation needs no safety check.
            lambda reservation: reservation.will_happen and not working_hours.covers(reservation),
        ),
        # The guarantor can re-plan a freed slot.
        HeadsUpRule("Cancelled", lambda reservation: not reservation.will_happen),
    ]


def heads_up_matches(reservations: list[Reservation], working_hours: WorkingHours) -> list[HeadsUpMatch]:
    """The rules that match, in a fixed order, each with its reservations in the order given."""
    matches = [
        HeadsUpMatch(rule.title, [r for r in reservations if rule.matches(r)]) for rule in _rules(working_hours)
    ]
    return [match for match in matches if match.reservations]
