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
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any, Protocol

from nano_standup.booking_source import InstrumentNotFound
from nano_standup.domain import Guarantor, Instrument, Reservation, Week

Params = dict[str, str | int | list[str]]

PAGE_SIZE = 50
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
            self._token = str(self._send("/api/login", request)["accessToken"])
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
            id=item["id"],
            name=item.get("ge_nameen") or item.get("name") or alias,
            alias=alias,
            guarantor=Guarantor(name=_person_name(guarantor_json) or guarantor_json.get("fullname") or "", email=email),
        )

    def get_reservations(self, instrument_id: str, week: Week) -> list[Reservation]:
        ids = [
            item["id"]
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
            if reservation_id:
                experiment_plans[reservation_id] = item.get("new_plan_experimentu_new") or ""
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
    contact = item.get("ge_realised_for_contactid") or {}
    name = _person_name(contact)
    return Reservation(
        id=item["id"],
        start=datetime.fromisoformat(item["scheduledstart"]),
        end=datetime.fromisoformat(item["scheduledend"]),
        user_id=contact.get("id") or "",
        user_name=name or "Unknown user",
        project_code=(item.get("subject") or "").strip(),
        experiment_plan=experiment_plan,
    )


def _person_name(person: dict[str, Any]) -> str:  # Any: parsed JSON.
    """"First Last". The booking system's full name is "Last, First"."""
    return f"{person.get('firstname') or ''} {person.get('lastname') or ''}".strip()
