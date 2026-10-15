"""An in-memory stand-in for the booking API, with the quirks the real one has. Made-up data only."""

from typing import Any

from nano_standup.booking_api import (
    CANCELLED_STATE,
    CANCELLED_STATUS,
    REQUESTED_STATE,
    REQUESTED_STATUS,
    RESERVED_STATE,
    RESERVED_STATUS,
    BookingApiError,
    Params,
)

MAX_PAGE_SIZE = 50  # The real API fails with a server error on larger pages when it expands activity parties.
# The real API fails with a server error on some pages of the reservation list when it expands the user.
# It works on a single reservation.
LIST_SCOPES_THAT_FAIL = {"ge_realised_for_contactid"}
# The statecode that goes with each statuscode, as in real data.
STATECODES = {RESERVED_STATUS: RESERVED_STATE, REQUESTED_STATUS: REQUESTED_STATE, CANCELLED_STATUS: CANCELLED_STATE}


class FakeBookingApi:
    def __init__(self) -> None:
        self.instruments: dict[str, dict[str, Any]] = {}  # Any: mirrors the API's JSON.
        self.reservations: list[dict[str, Any]] = []  # Any: mirrors the API's JSON.
        self.recordings: list[dict[str, Any]] = []  # Any: mirrors the API's JSON.
        # How many recordings the pages of /api/recording held, to check that reads stay small.
        self.recordings_read = 0

    def get(self, path: str, params: Params) -> Any:  # Any: mirrors the JSON shape of the real API.
        if path.startswith("/api/equipment/"):
            item = self.instruments.get(path.removeprefix("/api/equipment/"))
            if item is None:
                raise BookingApiError(path, 404, "Not Found")
            return item
        if path == "/api/service-appointment":
            if LIST_SCOPES_THAT_FAIL & set(str(params.get("scope", "")).split(",")):
                raise BookingApiError(path, 500, "Internal Server Error")
            listed = self._page(path, self.reservations, params, {"scheduledstart", "scheduledend"})
            return [{k: v for k, v in x.items() if k != "ge_realised_for_contactid"} for x in listed]
        if path.startswith("/api/service-appointment/"):
            reservation_id = path.removeprefix("/api/service-appointment/")
            for item in self.reservations:
                if item["id"] == reservation_id:
                    return {k: v for k, v in item.items() if k != "activity_parties"}
            raise BookingApiError(path, 404, "Not Found")
        if path == "/api/recording":
            page = self._page(path, self.recordings, params, {"new_start"})
            self.recordings_read += len(page)
            return page
        raise BookingApiError(path, 404, "Not Found")

    def _page(self, path: str, items: list[dict[str, Any]], params: Params, time_fields: set[str]) -> list[dict[str, Any]]:  # Any: JSON.
        limit = int(str(params.get("limit", 20)))
        if limit > MAX_PAGE_SIZE:
            raise BookingApiError(path, 500, "Internal Server Error")
        page = int(str(params.get("page", 1)))
        filters = params.get("filters[]", [])
        matching = [x for x in items if _matches(x, filters if isinstance(filters, list) else [str(filters)], time_fields)]
        return matching[(page - 1) * limit : page * limit]


def _matches(item: dict[str, Any], filters: list[str], time_fields: set[str]) -> bool:  # Any: mirrors the API's JSON.
    for f in filters:
        column, operator, value = f.split(":", 2)
        if column not in time_fields:
            raise AssertionError(f"FakeBookingApi does not support filter {f!r}")
        # A row with no value in the column never matches a lt or gt filter.
        if item.get(column) is None:
            return False
        if operator == "gt" and not item[column] > value:
            return False
        if operator == "lt" and not item[column] < value:
            return False
        if operator not in ("gt", "lt"):
            raise AssertionError(f"FakeBookingApi does not support operator in {f!r}")
    return True


def instrument_json(id: str, alias: str, name_en: str | None, name_cz: str, guarantor: dict[str, Any] | None) -> dict[str, Any]:  # Any: mirrors the API's JSON.
    return {"id": id, "ge_alias": alias, "ge_nameen": name_en, "name": name_cz, "ge_ownerid": guarantor}


def guarantor_json(firstname: str, lastname: str, email: str | None) -> dict[str, Any]:  # Any: mirrors the API's JSON.
    return {
        "id": f"sysuser-{lastname}",
        "firstname": firstname,
        "lastname": lastname,
        "fullname": f"{lastname}, {firstname}",
        "internalemailaddress": email,
    }


SEM_ID = "equip-sem"


def sem_json(guarantor_email: str = "hana.testova@example.org") -> dict[str, Any]:  # Any: mirrors the API's JSON.
    """The made-up microscope most tests use."""
    return instrument_json(SEM_ID, "TEST-SEM", "Test Electron Microscope", "Testovaci mikroskop", guarantor_json("Hana", "Testova", guarantor_email))


def reservation_json(
    id: str,
    instrument_id: str,
    start: str,
    end: str,
    subject: str = "2026-000001/RG-1-01",
    firstname: str = "Petr",
    lastname: str = "Vymysleny",
    statuscode: int = RESERVED_STATUS,
    note: str | None = None,
    private_note: str | None = None,
) -> dict[str, Any]:  # Any: mirrors the API's JSON.
    """The statecode follows the statuscode, as in real data. An unknown statuscode gets the reserved statecode."""
    return {
        "id": id,
        "subject": subject,
        "statecode": STATECODES.get(statuscode, STATECODES[RESERVED_STATUS]),
        "statuscode": statuscode,
        "description": note,
        "ge_privatedescription": private_note,
        "scheduledstart": start,
        "scheduledend": end,
        "activity_parties": [
            {"partyid": {"id": f"contact-{firstname}"}, "participationtypemask": 5},
            {"partyid": {"id": instrument_id}, "participationtypemask": 10},
        ],
        "ge_realised_for_contactid": {"id": f"contact-{firstname}-{lastname}", "firstname": firstname, "lastname": lastname},
    }


def recording(reservation_id: str | None, start: str, experiment_plan: str | None) -> dict[str, Any]:  # Any: mirrors the API's JSON.
    session = {"id": f"session-{reservation_id}", "ge_serviceappointmentid": {"id": reservation_id} if reservation_id else None}
    return {"id": f"rec-{reservation_id}", "new_start": start, "new_plan_experimentu_new": experiment_plan, "ge_sessionid": session}
