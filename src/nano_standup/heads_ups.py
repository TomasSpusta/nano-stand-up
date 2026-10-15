"""Heads-up rules: fixed rules that pick the reservations of the week a guarantor may want to act on.

The brief shows each rule that matches a reservation of the week, in the order given here.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from nano_standup.domain import (
    NO_USER_FACTS,
    Certificate,
    Reservation,
    ReservationStatus,
    UserFacts,
    WorkingHours,
    day_month_year,
)

NEW_USER = "New user"
NO_VALID_CERTIFICATE = "No valid certificate"


@dataclass(frozen=True)
class HeadsUpSettings:
    """The limits the heads-up rules use, from the settings."""

    # A reservation outside these hours may need an after-hours safety check.
    working_hours: WorkingHours
    # A user's first reservations on an instrument, up to this many, are by a new user.
    new_user_reservations: int


@dataclass(frozen=True)
class Finding:
    """What a rule found about one reservation."""

    # Shown after the user in the heads-up, such as "no. 2". Empty when the rule's title says it all.
    detail: str = ""
    # Shown on the reservation's row in the list. Empty when the row shows it already, as with the status.
    row_mark: str = ""


@dataclass(frozen=True)
class HeadsUpRule:
    title: str
    # A finding when the rule matches the reservation, or None.
    check: Callable[[Reservation], Finding | None]

    def matches(self, reservation: Reservation) -> bool:
        return self.check(reservation) is not None


def _when(condition: Callable[[Reservation], bool]) -> Callable[[Reservation], Finding | None]:
    """A check that finds nothing more than that the reservation matches."""
    return lambda reservation: Finding() if condition(reservation) else None


# The counts use this rule too, so the heads-up and the "waiting for your approval" count always agree.
WAITING_FOR_APPROVAL = HeadsUpRule(
    "Waiting for your approval", _when(lambda reservation: reservation.status is ReservationStatus.REQUESTED)
)


def heads_up_rules(
    settings: HeadsUpSettings, guarantor_user_id: str, user_facts: Mapping[str, UserFacts]
) -> list[HeadsUpRule]:
    """`user_facts` by user id. A user without facts has no certificate and no reservations before."""
    working_hours = settings.working_hours

    def facts_of(reservation: Reservation) -> UserFacts:
        return user_facts.get(reservation.user_id, NO_USER_FACTS)

    # First the reservations the guarantor has to approve, as in the counts.
    return [
        WAITING_FOR_APPROVAL,
        HeadsUpRule("No experiment plan", _when(lambda reservation: reservation.is_missing_experiment_plan)),
        HeadsUpRule(
            f"Outside working hours ({working_hours.start:%H:%M} to {working_hours.end:%H:%M}, Monday to Friday)",
            # A cancelled reservation needs no safety check.
            _when(lambda reservation: reservation.is_not_cancelled and not working_hours.covers(reservation)),
        ),
        HeadsUpRule(
            NEW_USER, lambda reservation: _new_user(reservation, facts_of(reservation), settings.new_user_reservations)
        ),
        HeadsUpRule(
            NO_VALID_CERTIFICATE,
            lambda reservation: _no_valid_certificate(reservation, facts_of(reservation), guarantor_user_id),
        ),
        # The guarantor can re-plan a freed slot.
        HeadsUpRule("Cancelled", _when(lambda reservation: reservation.status is ReservationStatus.CANCELLED)),
    ]


def _new_user(reservation: Reservation, facts: UserFacts, limit: int) -> Finding | None:
    """One of the user's first `limit` reservations since the start of their earliest certificate.

    A user with no certificate is not a new user: the certificate rule points them out.
    """
    if not reservation.is_not_cancelled or not facts.certificates:
        return None
    since = min(certificate.valid_from for certificate in facts.certificates)
    if reservation.start.date() < since:
        return None
    before = sum(1 for start in facts.reservation_starts if since <= start.date() and start < reservation.start)
    number = before + 1
    if number > limit:
        return None
    return Finding(detail=f"no. {number}", row_mark=f"{NEW_USER}, no. {number}")


def _no_valid_certificate(reservation: Reservation, facts: UserFacts, guarantor_user_id: str) -> Finding | None:
    """No certificate of the user covers the reservation start. The guarantor's own reservations are left out."""
    is_guarantor = bool(guarantor_user_id) and reservation.user_id == guarantor_user_id
    if not reservation.is_not_cancelled or is_guarantor:
        return None
    if any(certificate.covers(reservation.start) for certificate in facts.certificates):
        return None
    return Finding(detail=_why_not_valid(facts.certificates, reservation), row_mark=NO_VALID_CERTIFICATE)


def _why_not_valid(certificates: list[Certificate], reservation: Reservation) -> str:
    """Why no certificate covers the reservation: "missing", "expired 31. 5. 2026", or "valid from 2. 11. 2026".

    "expired" gives the latest end before the reservation. "valid from" gives the earliest start after it.
    """
    if not certificates:
        return "missing"
    day = reservation.start.date()
    ended = [c.valid_to for c in certificates if c.valid_to is not None and c.valid_to < day]
    if ended:
        return f"expired {day_month_year(max(ended))}"
    # None has ended, and none covers the day, so all start later.
    return f"valid from {day_month_year(min(c.valid_from for c in certificates))}"
