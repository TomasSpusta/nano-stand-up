"""Heads-up rules: fixed rules that pick the reservations of the week a guarantor may want to act on.

The brief shows each rule that matches a reservation of the week, in the order given here.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date

from nano_standup.domain import (
    NO_USER_FACTS,
    Certificate,
    Reservation,
    ReservationStatus,
    UserFacts,
    WorkingHours,
)
from nano_standup.formatting import day_month_year

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


def _when(condition: Callable[[Reservation], bool]) -> Callable[[Reservation], Finding | None]:
    """A check that finds nothing more than that the reservation matches."""
    return lambda reservation: Finding() if condition(reservation) else None


def _unless_cancelled(check: Callable[[Reservation], Finding | None]) -> Callable[[Reservation], Finding | None]:
    """A check that never matches a cancelled reservation."""
    return lambda reservation: None if reservation.is_cancelled else check(reservation)


# The counts use this rule too, so the heads-up and the "waiting for your approval" count always agree.
WAITING_FOR_APPROVAL = HeadsUpRule(
    "Waiting for your approval", _when(lambda reservation: reservation.status is ReservationStatus.REQUESTED)
)


def heads_up_rules(
    settings: HeadsUpSettings, guarantor_user_id: str | None, user_facts: Mapping[str, UserFacts]
) -> list[HeadsUpRule]:
    """`user_facts` by user id. A user without facts has no certificate and no reservations before.

    A cancelled reservation matches only the Cancelled rule: it needs no safety check, and its user needs no check.
    """
    working_hours = settings.working_hours

    def facts_of(reservation: Reservation) -> UserFacts:
        return user_facts.get(reservation.user_id, NO_USER_FACTS)

    # First the reservations the guarantor has to approve, as in the counts.
    return [
        WAITING_FOR_APPROVAL,
        HeadsUpRule("No experiment plan", _when(lambda reservation: reservation.is_missing_experiment_plan)),
        HeadsUpRule(
            f"Outside working hours ({working_hours.start:%H:%M} to {working_hours.end:%H:%M}, Monday to Friday)",
            _unless_cancelled(_when(lambda reservation: not working_hours.covers(reservation))),
        ),
        HeadsUpRule(
            NEW_USER,
            _unless_cancelled(
                lambda reservation: _new_user(reservation, facts_of(reservation), settings.new_user_reservations)
            ),
        ),
        HeadsUpRule(
            NO_VALID_CERTIFICATE,
            _unless_cancelled(
                lambda reservation: _no_valid_certificate(reservation, facts_of(reservation), guarantor_user_id)
            ),
        ),
        # The guarantor can re-plan a freed slot.
        HeadsUpRule("Cancelled", _when(lambda reservation: reservation.is_cancelled)),
    ]


def _new_user(reservation: Reservation, facts: UserFacts, limit: int) -> Finding | None:
    """One of the user's first `limit` reservations since the start of their earliest certificate.

    A user with no certificate is not a new user: the certificate rule points them out.
    """
    if not facts.certificates:
        return None
    since = min(certificate.valid_from for certificate in facts.certificates)
    if reservation.start.date() < since:
        return None
    before = sum(1 for start in facts.reservation_starts if since <= start.date() and start < reservation.start)
    number = before + 1
    if number > limit:
        return None
    return Finding(detail=f"no. {number}", row_mark=f"{NEW_USER}, no. {number}")


def _no_valid_certificate(reservation: Reservation, facts: UserFacts, guarantor_user_id: str | None) -> Finding | None:
    """No certificate of the user covers the reservation start. The guarantor's own reservations are left out."""
    if reservation.user_id == guarantor_user_id:
        return None
    if any(certificate.covers(reservation.start) for certificate in facts.certificates):
        return None
    return Finding(detail=_why_not_valid(facts.certificates, reservation.start.date()), row_mark=NO_VALID_CERTIFICATE)


def _why_not_valid(certificates: list[Certificate], day: date) -> str:
    """Why no certificate covers the reservation: "missing", "expired 31. 5. 2026", or "valid from 2. 11. 2026".

    `day` is the reservation's start day. "expired" gives the latest end before it, "valid from" the earliest start.
    """
    if not certificates:
        return "missing"
    ended = [c.valid_to for c in certificates if c.valid_to is not None and c.valid_to < day]
    if ended:
        return f"expired {day_month_year(max(ended))}"
    # None has ended, and none covers the day, so all start later.
    return f"valid from {day_month_year(min(c.valid_from for c in certificates))}"
