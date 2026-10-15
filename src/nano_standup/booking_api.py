"""The real booking source, over the CEITEC booking API. Read-only: it only sends GET requests (and the login).

API quirks it hides:
- A reservation's instrument is in its activity parties, and the API cannot filter by it,
  so it loads the week's reservations and filters them here.
- Expanding activity parties fails on large pages, so it reads pages of 50.
- Expanding the user on the reservation list fails on some pages, so it loads each matching reservation on its own.
- The experiment plan is on a recording, which points to a session, which points to the reservation.
- Filters know only gt and lt, not "greater or equal".
"""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any, Protocol

from nano_standup.booking_source import InstrumentNotFound
from nano_standup.domain import Guarantor, Instrument, Reservation, ReservationStatus, Week

log = logging.getLogger(__name__)

Params = dict[str, str | int | list[str]]

PAGE_SIZE = 50
# Reservation statecode and statuscode in the booking system, which is built on Microsoft Dynamics.
# Only these three pairs were seen in real data.
# The booking system labels the first two "Rezervováno" (Reserved) and "Požadováno" (Requested).
RESERVED_STATE, RESERVED_STATUS = 3, 4
REQUESTED_STATE, REQUESTED_STATUS = 0, 1
CANCELLED_STATE, CANCELLED_STATUS = 2, 9
# A recording starts about when its reservation does. One day of margin each side is enough to find it.
RECORDING_MARGIN = timedelta(days=1)


class BookingApiError(RuntimeError):
    def __init__(self, path: str, status: int | None, reason: str) -> None:
        """`status` is the HTTP status, or None when no answer came."""
        detail = f"HTTP {status} {reason}" if status is not None else reason
        super().__init__(f"Booking API request {path} failed: {detail}")
        self.path = path
        self.status = status


class BookingDataError(RuntimeError):
    """The booking API returned data the service cannot use."""


class BookingApi(Protocol):
    def get(self, path: str, params: Params) -> Any: ...  # Any: parsed JSON.


class HttpBookingApi:
    def __init__(self, base_url: str, api_key: str, timeout_seconds: float = 60) -> None:
        self._base_url = base_url.rstrip("/").removesuffix("/api")
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._token: str | None = None

    def get(self, path: str, params: Params) -> Any:  # Any: parsed JSON.
        query = urllib.parse.urlencode(params, doseq=True)
        request = urllib.request.Request(
            f"{self._base_url}{path}?{query}", headers={"Authorization": f"Bearer {self._login()}"}
        )
        return self._send(path, request)

    def _login(self) -> str:
        if self._token is None:
            request = urllib.request.Request(
                f"{self._base_url}/api/login",
                data=json.dumps({"apiKey": self._api_key}).encode(),
                headers={"Content-Type": "application/json"},
            )
            self._token = str(_required(self._send("/api/login", request), "accessToken", "The answer to /api/login"))
        return self._token

    def _send(self, path: str, request: urllib.request.Request) -> Any:  # Any: parsed JSON.
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise BookingApiError(path, error.code, str(error.reason)) from error
        except (urllib.error.URLError, TimeoutError) as error:
            reason = error.reason if isinstance(error, urllib.error.URLError) else error
            raise BookingApiError(path, None, str(reason)) from error


class ApiBookingSource:
    def __init__(self, api: BookingApi) -> None:
        self._api = api

    def get_instrument(self, instrument_id: str) -> Instrument:
        path = f"/api/equipment/{urllib.parse.quote(instrument_id)}"
        try:
            item = self._api.get(path, {"scope": "ge_ownerid"})
        except BookingApiError as error:
            if error.status == 404:
                raise InstrumentNotFound(f"No instrument with id {instrument_id!r} in the booking system") from error
            raise
        alias = item.get("ge_alias") or ""
        guarantor_json = item.get("ge_ownerid") or {}
        email = (guarantor_json.get("internalemailaddress") or "").strip()
        if not email:
            raise BookingDataError(f"Instrument {alias} ({instrument_id}) has no guarantor email in the booking system")
        return Instrument(
            id=_required(item, "id", f"Instrument {alias} ({instrument_id})"),
            name=item.get("ge_nameen") or item.get("name") or alias,
            alias=alias,
            guarantor=Guarantor(name=_person_name(guarantor_json) or guarantor_json.get("fullname") or "", email=email),
        )

    def get_reservations(self, instrument_id: str, week: Week) -> list[Reservation]:
        ids = [
            _required(item, "id", "A reservation in the list")
            for item in self._pages(
                "/api/service-appointment",
                _time_window("scheduledstart", week.start, week.end),
                scope="activity_parties",
            )
            if any((party.get("partyid") or {}).get("id") == instrument_id for party in item.get("activity_parties") or [])
        ]
        if not ids:
            return []
        experiment_plans = self._experiment_plans(week)
        return [
            _reservation(
                self._api.get(f"/api/service-appointment/{urllib.parse.quote(reservation_id)}", {"scope": "ge_realised_for_contactid"}),
                experiment_plans.get(reservation_id, ""),
            )
            for reservation_id in ids
        ]

    def _experiment_plans(self, week: Week) -> dict[str, str]:
        """Experiment plan by reservation id."""
        experiment_plans: dict[str, str] = {}
        window = _time_window("new_start", week.start - RECORDING_MARGIN, week.end + RECORDING_MARGIN)
        for item in self._pages("/api/recording", window, scope="ge_sessionid"):
            session = item.get("ge_sessionid") or {}
            reservation_id = (session.get("ge_serviceappointmentid") or {}).get("id")
            experiment_plan = item.get("new_plan_experimentu_new") or ""
            # A reservation can have more than one recording. An empty experiment plan never hides a filled one.
            if reservation_id and (experiment_plan.strip() or reservation_id not in experiment_plans):
                experiment_plans[reservation_id] = experiment_plan
        return experiment_plans

    def _pages(self, path: str, filters: list[str], scope: str) -> Iterator[dict[str, Any]]:  # Any: parsed JSON.
        page = 1
        while True:
            page_items = self._api.get(path, {"filters[]": filters, "scope": scope, "limit": PAGE_SIZE, "page": page})
            if not page_items:
                return
            yield from page_items
            page += 1


def _time_window(field: str, start: datetime, end: datetime) -> list[str]:
    """From start (included) to end (left out). The API has no "greater or equal", so start goes back one second."""
    return [f"{field}:gt:{start - timedelta(seconds=1):%Y-%m-%dT%H:%M:%S}", f"{field}:lt:{end:%Y-%m-%dT%H:%M:%S}"]


def _reservation(item: dict[str, Any], experiment_plan: str) -> Reservation:  # Any: parsed JSON.
    reservation_id = _required(item, "id", "A reservation")
    what = f"Reservation {reservation_id}"
    contact = item.get("ge_realised_for_contactid") or {}
    name = _person_name(contact)
    return Reservation(
        id=reservation_id,
        start=_local_time(item, "scheduledstart", what),
        end=_local_time(item, "scheduledend", what),
        user_id=contact.get("id") or "",
        user_name=name or "Unknown user",
        project_code=(item.get("subject") or "").strip(),
        experiment_plan=experiment_plan,
        status=_status(item, what),
        note=item.get("description") or "",
        private_note=item.get("ge_privatedescription") or "",
    )


def _status(item: dict[str, Any], what: str) -> ReservationStatus:  # Any: parsed JSON.
    statecode, statuscode = item.get("statecode"), item.get("statuscode")
    if statecode == CANCELLED_STATE or statuscode == CANCELLED_STATUS:
        return ReservationStatus.CANCELLED
    if statuscode == REQUESTED_STATUS:
        return ReservationStatus.REQUESTED
    if statuscode != RESERVED_STATUS:
        log.warning("%s has an unknown statuscode %r (statecode %r); the brief shows it as reserved", what, statuscode, statecode)
    return ReservationStatus.RESERVED


def _required(item: Any, key: str, what: str) -> Any:  # Any: parsed JSON.
    """`item[key]`, or a BookingDataError that names `what` when it is missing or empty."""
    value = item.get(key) if isinstance(item, dict) else None
    if value is None or value == "":
        raise BookingDataError(f"{what} has no {key!r} in the booking API answer")
    return value


def _local_time(item: dict[str, Any], key: str, what: str) -> datetime:  # Any: parsed JSON.
    """A naive Prague local time. A time zone is refused: the rest of the service could not compare it."""
    text = str(_required(item, key, what))
    try:
        value = datetime.fromisoformat(text)
    except ValueError as error:
        raise BookingDataError(f"{what} has a {key} that is not a date and time: {text!r}") from error
    if value.tzinfo is not None:
        raise BookingDataError(f"{what} has a {key} with a time zone ({text!r}); the service expects Prague local time without one")
    return value


def _person_name(person: dict[str, Any]) -> str:  # Any: parsed JSON.
    """"First Last". The booking system's full name is "Last, First"."""
    return f"{person.get('firstname') or ''} {person.get('lastname') or ''}".strip()
