"""Opt-in contract check of the real booking source against the live booking API. Read-only.

It catches API changes the fake would hide.
It is skipped by default (marker `live_api`) and when the booking settings are not in the environment.
Run it with `uv run --env-file .env pytest -m live_api`; see the README.

The repo is public and the booking data is personal, so failures show only counts, field names, ids, and weeks,
never names, emails, experiment plans, notes, or the API key.
Every failure goes through live_api_safety, so no traceback, and no frame with raw JSON, is printed.
"""

import logging
import os
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import pytest

from nano_standup.booking_api import (
    PAGE_SIZE,
    UNKNOWN_STATUSCODE_LOG,
    UNKNOWN_USER_NAME,
    ApiBookingSource,
    HttpBookingApi,
)
from nano_standup.booking_source import BookingSource
from nano_standup.domain import Reservation, Week

from live_api_safety import expect, failures_without_data, read_only

pytestmark = pytest.mark.live_api

INSTRUMENT_VARIABLE = "LIVE_API_INSTRUMENT_ID"
REQUIRED_VARIABLES = ("BOOKING_API_BASE_URL", "BOOKING_API_KEY", INSTRUMENT_VARIABLE)
# How far back to look for a week with reservations that have an experiment plan.
WEEKS_BACK = 12
# How many of the week's users to check certificates and past reservations for, to keep the run short.
USERS_TO_CHECK = 5
# Keys of a reservation that the brief reads but that may be empty, so the source cannot tell a renamed key.
RESERVATION_NOTE_KEYS = ("description", "ge_privatedescription")
INSTRUMENT_NAME_KEYS = ("ge_nameen", "name")


@pytest.fixture(scope="module")
def instrument_id() -> str:
    missing = [name for name in REQUIRED_VARIABLES if not os.environ.get(name, "").strip()]
    if missing:
        pytest.skip(f"Live API check needs {', '.join(missing)} in the environment")
    return os.environ[INSTRUMENT_VARIABLE].strip()


@pytest.fixture(scope="module")
def api(instrument_id: str) -> Iterator[HttpBookingApi]:
    """The real HTTP layer, guarded so that any request other than a GET or the login fails before it is sent."""
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(urllib.request, "urlopen", read_only(urllib.request.urlopen))
        yield HttpBookingApi(os.environ["BOOKING_API_BASE_URL"].strip(), os.environ["BOOKING_API_KEY"].strip())


@dataclass(frozen=True)
class LiveWeek:
    week: Week
    reservations: list[Reservation]
    # Reservations whose statuscode the source does not know, from its log.
    unknown_statuses: int


class _UnknownStatusCounter(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        self.count += record.msg == UNKNOWN_STATUSCODE_LOG


@pytest.fixture(scope="module")
def live_week(api: HttpBookingApi, instrument_id: str) -> LiveWeek:
    """The latest week, from this one back, whose reservations have at least one experiment plan. Read once.

    About a third of experiment plans are empty, so a week with reservations but no experiment plan is passed over.
    When no week of `WEEKS_BACK` has one, the experiment plans are surely lost, and the check fails.
    """
    source = ApiBookingSource(api)
    counter = _UnknownStatusCounter()
    logger = logging.getLogger("nano_standup.booking_api")
    logger.addHandler(counter)
    this_week = Week.containing(date.today())
    weeks_with_reservations = 0
    try:
        with failures_without_data(f"Instrument {instrument_id}, reading the last {WEEKS_BACK} weeks"):
            for weeks_back in range(WEEKS_BACK):
                week = Week(this_week.monday - timedelta(weeks=weeks_back))
                reservations = source.get_reservations(instrument_id, week)
                weeks_with_reservations += bool(reservations)
                if any(r.experiment_plan.strip() for r in reservations):
                    return LiveWeek(week, reservations, counter.count)
    finally:
        logger.removeHandler(counter)
    expect(
        weeks_with_reservations > 0, f"Instrument {instrument_id} has no reservations in the last {WEEKS_BACK} weeks"
    )
    pytest.fail(
        f"Instrument {instrument_id}: {weeks_with_reservations} of the last {WEEKS_BACK} weeks have reservations, "
        "but none has an experiment plan",
        pytrace=False,
    )


def test_the_real_source_still_gives_what_the_brief_needs(
    api: HttpBookingApi, instrument_id: str, live_week: LiveWeek
) -> None:
    week, reservations = live_week.week, live_week.reservations
    where = f"Instrument {instrument_id}, week of {week.monday}"
    # Typed as the interface, so mypy checks that the real source still follows it, as the fake does.
    source: BookingSource = ApiBookingSource(api)
    with failures_without_data(where):
        instrument = source.get_instrument(instrument_id)
        expect(instrument.id == instrument_id, f"{where}: the instrument has another id than the one asked for")
        expect(bool(instrument.alias.strip()), f"{where}: the instrument has no alias (ge_alias)")
        expect("@" in instrument.guarantor.email, f"{where}: the guarantor's email has no @")
        expect(bool(instrument.guarantor.name.strip()), f"{where}: the guarantor has no name")
        expect(instrument.guarantor.user_id is not None, f"{where}: the guarantor has no contact, so no user id")
        # The source falls back to the alias, so only the raw answer shows a lost name.
        raw_instrument = api.get(f"/api/equipment/{urllib.parse.quote(instrument_id)}", {})
        expect(
            any(str(raw_instrument.get(key) or "").strip() for key in INSTRUMENT_NAME_KEYS),
            f"{where}: the instrument has none of {', '.join(INSTRUMENT_NAME_KEYS)}",
        )

        for r in reservations:
            expect(bool(r.id), f"{where}: a reservation has no id")
            expect(r.start < r.end, f"{where}: reservation {r.id} does not end after it starts")
            expect(week.overlaps(r), f"{where}: reservation {r.id} is not inside the week")
            expect(bool(r.user_id), f"{where}: reservation {r.id} has no user id")
            expect(r.user_name != UNKNOWN_USER_NAME, f"{where}: reservation {r.id} has no user name")
        with_project_code = sum(1 for r in reservations if r.project_code.strip())
        expect(
            with_project_code > 0,
            f"{where}: none of the {len(reservations)} reservations has a project code (subject)",
        )
        expect(
            live_week.unknown_statuses == 0,
            f"{where}: {live_week.unknown_statuses} reservations have a statuscode the source does not know",
        )
        _expect_note_keys(api, reservations, where)

        users = sorted({r.user_id for r in reservations if not r.is_cancelled})[:USERS_TO_CHECK]
        expect(bool(users), f"{where}: the week has no reservations that are not cancelled")
        users_with_certificates = 0
        users_with_history = 0
        for user_id in users:
            facts = source.get_user_facts(instrument_id, user_id)
            for certificate in facts.certificates:
                expect(
                    certificate.valid_to is None or certificate.valid_from <= certificate.valid_to,
                    f"{where}: a certificate of user {user_id} ends before it starts",
                )
            users_with_certificates += bool(facts.certificates)
            week_starts = {r.start for r in reservations if r.user_id == user_id and not r.is_cancelled}
            # The past reservations come from sessions filtered by instrument and user.
            expect(
                week_starts <= set(facts.reservation_starts),
                f"{where}: the past reservations of user {user_id} miss some of their reservations in the week",
            )
            users_with_history += any(start < week.start for start in facts.reservation_starts)
        expect(
            users_with_certificates > 0,
            f"{where}: none of the {len(users)} users checked has a certificate for the instrument",
        )
        expect(
            users_with_history > 0, f"{where}: none of the {len(users)} users checked has a reservation before the week"
        )

    statuses = Counter(r.status.value for r in reservations)
    print(
        f"Live API check: week of {week.monday}, {len(reservations)} reservations "
        f"({', '.join(f'{count} {status}' for status, count in sorted(statuses.items()))}), "
        f"{sum(1 for r in reservations if r.experiment_plan.strip())} with an experiment plan, "
        f"{len(users)} users checked, {users_with_certificates} with certificates, {users_with_history} with history"
    )


def test_the_unlisted_session_filters_still_filter(
    api: HttpBookingApi, instrument_id: str, live_week: LiveWeek
) -> None:
    """The API spec does not list the session filters by instrument and by user, so they could go without notice.

    A filter the API ignores gives sessions of other instruments or users, so each filter is checked on its own.
    """
    where = f"Instrument {instrument_id}, week of {live_week.week.monday}"
    user_id = next((r.user_id for r in live_week.reservations if not r.is_cancelled), "")
    expect(bool(user_id), f"{where}: the week has no reservations that are not cancelled")

    by_instrument = f"ge_equipmentid:eq:{instrument_id}"
    by_user = f"ge_realised_for_contactid:eq:{user_id}"
    with failures_without_data(where):
        for filters, instrument_must_match, user_must_match in (
            ([by_instrument], True, False),
            ([by_user], False, True),
            ([by_instrument, by_user], True, True),
        ):
            sessions = api.get("/api/session", {"filters[]": filters, "limit": PAGE_SIZE, "page": 1})
            expect(isinstance(sessions, list), f"{where}: the session list with filters {filters} is not a list")
            expect(bool(sessions), f"{where}: the session list with filters {filters} is empty")
            for session in sessions:
                if instrument_must_match:
                    expect(
                        _reference_id(session, "ge_equipmentid") == instrument_id,
                        f"{where}: filters {filters} gave session {session.get('id')} of another instrument",
                    )
                if user_must_match:
                    expect(
                        _reference_id(session, "ge_realised_for_contactid") == user_id,
                        f"{where}: filters {filters} gave session {session.get('id')} of another user",
                    )


def _expect_note_keys(api: HttpBookingApi, reservations: list[Reservation], where: str) -> None:
    """The note keys are still in the raw answer of at least one reservation. Only their presence is checked.

    The source reads a missing key as an empty note, so only the raw answer shows a renamed key.
    """
    missing = set(RESERVATION_NOTE_KEYS)
    for r in reservations:
        item = api.get(f"/api/service-appointment/{urllib.parse.quote(r.id)}", {})
        missing -= item.keys()
        if not missing:
            return
    expect(False, f"{where}: none of the {len(reservations)} reservations has the keys {', '.join(sorted(missing))}")


def _reference_id(item: dict[str, Any], key: str) -> str | None:  # Any: parsed JSON.
    """The id a reference field points to. The API gives a reference as an object with an id."""
    value = item.get(key)
    if isinstance(value, dict):
        return value.get("id")
    return value if isinstance(value, str) else None
