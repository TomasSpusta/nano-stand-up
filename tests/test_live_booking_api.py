"""Opt-in contract check of the real booking source against the live booking API. Read-only.

It catches API changes the fake would hide.
It is skipped by default (marker `live_api`) and when the booking settings are not in the environment.
Run it with `uv run --env-file .env pytest -m live_api`; see the README.

The repo is public and the booking data is personal, so failure messages hold only counts, field names, and ids,
never names, emails, plans, notes, or the API key.
"""

import logging
import os
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

import pytest

from nano_standup.booking_api import LOGIN_PATH, PAGE_SIZE, ApiBookingSource, HttpBookingApi
from nano_standup.booking_source import BookingSource
from nano_standup.domain import Reservation, Week

pytestmark = pytest.mark.live_api

INSTRUMENT_VARIABLE = "LIVE_API_INSTRUMENT_ID"
REQUIRED_VARIABLES = ("BOOKING_API_BASE_URL", "BOOKING_API_KEY", INSTRUMENT_VARIABLE)
# How far back to look for a week with reservations that have an experiment plan.
WEEKS_BACK = 12
# How many of the week's users to check certificates and past reservations for, to keep the run short.
USERS_TO_CHECK = 5


def expect(ok: bool, message: str) -> None:
    """Fail with only `message`: no traceback, so no values of local variables reach the output."""
    if not ok:
        pytest.fail(message, pytrace=False)


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> HttpBookingApi:
    """The real HTTP layer, guarded so that any request other than a GET or the login POST fails before it is sent."""
    missing = [name for name in REQUIRED_VARIABLES if not os.environ.get(name, "").strip()]
    if missing:
        pytest.skip(f"Live API check needs {', '.join(missing)} in the environment")

    real_urlopen = urllib.request.urlopen

    def read_only_urlopen(request: Any, *args: Any, **kwargs: Any) -> Any:  # Any: matches urllib.request.urlopen.
        expect(isinstance(request, urllib.request.Request), "The live check sent a request that is not a Request")
        method = request.get_method()
        path = urllib.parse.urlsplit(request.full_url).path
        is_login = method == "POST" and path.endswith(LOGIN_PATH)
        # pytest.fail raises a BaseException, so the HTTP layer cannot catch it and go on.
        expect(method == "GET" or is_login, f"The live check sent {method} {path}; only GET and the login are allowed")
        return real_urlopen(request, *args, **kwargs)

    monkeypatch.setattr(urllib.request, "urlopen", read_only_urlopen)
    return HttpBookingApi(os.environ["BOOKING_API_BASE_URL"].strip(), os.environ["BOOKING_API_KEY"].strip())


@pytest.fixture
def instrument_id() -> str:
    return os.environ.get(INSTRUMENT_VARIABLE, "").strip()


def test_the_real_source_still_gives_what_the_brief_needs(
    api: HttpBookingApi, instrument_id: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="nano_standup.booking_api")
    # Typed as the interface, so mypy checks that the real source still follows it, as the fake does.
    source: BookingSource = ApiBookingSource(api)

    instrument = source.get_instrument(instrument_id)
    expect(instrument.id == instrument_id, "The instrument has another id than the one asked for")
    expect(bool(instrument.name.strip()), "The instrument has no name")
    expect(bool(instrument.alias.strip()), "The instrument has no alias")
    expect("@" in instrument.guarantor.email, "The guarantor's email has no @")
    expect(bool(instrument.guarantor.name.strip()), "The guarantor has no name")
    expect(instrument.guarantor.user_id is not None, "The guarantor has no contact, so no user id")

    week, reservations = _recent_week_with_experiment_plans(lambda w: source.get_reservations(instrument_id, w))
    for r in reservations:
        expect(bool(r.id), "A reservation has no id")
        expect(r.start < r.end, f"Reservation {r.id} does not end after it starts")
        expect(week.overlaps(r), f"Reservation {r.id} is not inside the week")
        expect(bool(r.user_id), f"Reservation {r.id} has no user id")
        expect(r.user_name != "Unknown user", f"Reservation {r.id} has no user name")
    with_project_code = sum(1 for r in reservations if r.project_code.strip())
    expect(with_project_code > 0, f"None of the week's {len(reservations)} reservations has a project code (subject)")
    unknown_statuses = [m for m in caplog.messages if "unknown statuscode" in m]
    expect(not unknown_statuses, f"{len(unknown_statuses)} reservations have a statuscode the source does not know")

    users = sorted({r.user_id for r in reservations if not r.is_cancelled})[:USERS_TO_CHECK]
    expect(bool(users), "The week has no reservations that are not cancelled")
    users_with_certificates = 0
    users_with_history = 0
    for user_id in users:
        facts = source.get_user_facts(instrument_id, user_id)
        for certificate in facts.certificates:
            expect(
                certificate.valid_to is None or certificate.valid_from <= certificate.valid_to,
                f"A certificate of user {user_id} ends before it starts",
            )
        users_with_certificates += bool(facts.certificates)
        week_starts = {r.start for r in reservations if r.user_id == user_id and not r.is_cancelled}
        # The past reservations come from sessions filtered by instrument and user.
        expect(
            week_starts <= set(facts.reservation_starts),
            f"The past reservations of user {user_id} miss some of their reservations in the week",
        )
        users_with_history += any(start < week.start for start in facts.reservation_starts)
    expect(users_with_certificates > 0, f"None of the {len(users)} users checked has a certificate for the instrument")
    expect(users_with_history > 0, f"None of the {len(users)} users checked has a reservation before the week")

    print(
        f"Live API check: week of {week.monday}, {len(reservations)} reservations, "
        f"{sum(1 for r in reservations if r.experiment_plan.strip())} with an experiment plan, "
        f"{len(users)} users checked, {users_with_certificates} with certificates, {users_with_history} with history"
    )


def test_the_unlisted_session_filters_still_filter(api: HttpBookingApi, instrument_id: str) -> None:
    """The API spec does not list the session filters by instrument and by user, so they could go without notice.

    A filter the API ignores gives sessions of other instruments or users, so each filter is checked on its own.
    """
    source = ApiBookingSource(api)
    _, reservations = _recent_week_with_experiment_plans(lambda w: source.get_reservations(instrument_id, w))
    user_id = next((r.user_id for r in reservations if not r.is_cancelled), None)
    expect(user_id is not None, "The week has no reservations that are not cancelled")
    assert user_id is not None  # For mypy.

    by_instrument = f"ge_equipmentid:eq:{instrument_id}"
    by_user = f"ge_realised_for_contactid:eq:{user_id}"
    for filters, instrument_must_match, user_must_match in (
        ([by_instrument], True, False),
        ([by_user], False, True),
        ([by_instrument, by_user], True, True),
    ):
        sessions = api.get("/api/session", {"filters[]": filters, "limit": PAGE_SIZE, "page": 1})
        expect(isinstance(sessions, list), f"The session list with filters {filters} is not a list")
        expect(bool(sessions), f"The session list with filters {filters} is empty")
        for session in sessions:
            if instrument_must_match:
                expect(
                    _reference_id(session, "ge_equipmentid") == instrument_id,
                    f"Filters {filters} gave session {session.get('id')} of another instrument",
                )
            if user_must_match:
                expect(
                    _reference_id(session, "ge_realised_for_contactid") == user_id,
                    f"Filters {filters} gave session {session.get('id')} of another user",
                )


def _recent_week_with_experiment_plans(
    get_reservations: Callable[[Week], list[Reservation]],
) -> tuple[Week, list[Reservation]]:
    """The latest week, from this one back, whose reservations have at least one experiment plan.

    About a third of plans are empty, so a week with reservations but no plan is skipped; but when no week of
    `WEEKS_BACK` has a plan, the plans are surely lost, and the check fails.
    """
    this_week = Week.containing(date.today())
    weeks_with_reservations = 0
    for weeks_back in range(WEEKS_BACK):
        week = Week(this_week.monday - timedelta(weeks=weeks_back))
        reservations = get_reservations(week)
        weeks_with_reservations += bool(reservations)
        if any(r.experiment_plan.strip() for r in reservations):
            return week, reservations
    expect(weeks_with_reservations > 0, f"The instrument has no reservations in the last {WEEKS_BACK} weeks")
    pytest.fail(
        f"{weeks_with_reservations} of the last {WEEKS_BACK} weeks have reservations, but none has an experiment plan",
        pytrace=False,
    )


def _reference_id(item: dict[str, Any], key: str) -> str | None:  # Any: parsed JSON.
    """The id a reference field points to. The API gives a reference as an object with an id."""
    value = item.get(key)
    if isinstance(value, dict):
        return value.get("id")
    return value if isinstance(value, str) else None
