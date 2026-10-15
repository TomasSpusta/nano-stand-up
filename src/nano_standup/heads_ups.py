"""Heads-up rules: fixed rules that pick the reservations of the week a guarantor may want to act on.

The brief shows each rule that matches a reservation of the week, in the order given here.
"""

from collections.abc import Callable
from dataclasses import dataclass

from nano_standup.domain import Reservation, ReservationStatus, WorkingHours


@dataclass(frozen=True)
class HeadsUpRule:
    title: str
    matches: Callable[[Reservation], bool]


def heads_up_rules(working_hours: WorkingHours) -> list[HeadsUpRule]:
    # First the reservations the guarantor has to approve, as in the counts.
    return [
        HeadsUpRule("Waiting for your approval", lambda reservation: reservation.status is ReservationStatus.REQUESTED),
        HeadsUpRule("No experiment plan", lambda reservation: reservation.will_happen_without_experiment_plan),
        HeadsUpRule(
            f"Outside working hours ({working_hours.start:%H:%M} to {working_hours.end:%H:%M}, Monday to Friday)",
            # A cancelled reservation needs no safety check.
            lambda reservation: reservation.will_happen and not working_hours.covers(reservation),
        ),
        # The guarantor can re-plan a freed slot.
        HeadsUpRule("Cancelled", lambda reservation: not reservation.will_happen),
    ]

