from datetime import date, datetime

import pytest

from nano_standup.booking_api import ApiBookingSource, BookingDataError
from nano_standup.booking_source import InstrumentNotFound
from nano_standup.domain import Guarantor, Week

from fake_booking_api import FakeBookingApi, equipment, owner, recording, service_appointment

SEM = "equip-sem"
LAB = "equip-lab"
WEEK = Week(date(2026, 10, 19))


@pytest.fixture
def api() -> FakeBookingApi:
    api = FakeBookingApi()
    api.equipment[SEM] = equipment(SEM, "TEST-SEM", "Test Electron Microscope", "Testovaci mikroskop", owner("Hana", "Testova", "Hana.Testova@example.org"))
    api.equipment[LAB] = equipment(LAB, "TEST-LAB", None, "Testovaci laborator", owner("Ota", "Zkusebni", "ota.zkusebni@example.org"))
    return api


def test_get_instrument_returns_its_names_and_guarantor_first_name_first(api: FakeBookingApi) -> None:
    instrument = ApiBookingSource(api).get_instrument(SEM)

    assert (instrument.id, instrument.name, instrument.alias) == (SEM, "Test Electron Microscope", "TEST-SEM")
    assert instrument.guarantor == Guarantor("Hana Testova", "Hana.Testova@example.org")


def test_get_instrument_uses_the_czech_name_when_there_is_no_english_one(api: FakeBookingApi) -> None:
    assert ApiBookingSource(api).get_instrument(LAB).name == "Testovaci laborator"


def test_get_instrument_raises_instrument_not_found_for_an_unknown_id(api: FakeBookingApi) -> None:
    with pytest.raises(InstrumentNotFound, match="no-such-id"):
        ApiBookingSource(api).get_instrument("no-such-id")


def test_get_instrument_refuses_an_instrument_without_a_guarantor_email(api: FakeBookingApi) -> None:
    api.equipment["equip-x"] = equipment("equip-x", "TEST-X", "Test X", "Test X", owner("Nikdo", "Nikdo", None))

    with pytest.raises(BookingDataError, match="TEST-X"):
        ApiBookingSource(api).get_instrument("equip-x")


def test_get_reservations_returns_the_instrument_week_with_user_project_and_plan(api: FakeBookingApi) -> None:
    api.reservations = [
        service_appointment("r1", SEM, "2026-10-20T09:00:00", "2026-10-20T11:30:00", subject="2026-123456/RG-1-01", firstname="Jana", lastname="Smyslena"),
        service_appointment("r2", LAB, "2026-10-20T09:00:00", "2026-10-20T10:00:00"),
    ]
    api.recordings = [recording("r1", "2026-10-20T09:00:00", "SEM imaging of thin films"), recording("r2", "2026-10-20T09:00:00", "Other")]

    [reservation] = ApiBookingSource(api).get_reservations(SEM, WEEK)

    assert reservation.id == "r1"
    assert (reservation.start, reservation.end) == (datetime(2026, 10, 20, 9, 0), datetime(2026, 10, 20, 11, 30))
    assert reservation.user_name == "Jana Smyslena"
    assert reservation.user_id == "contact-Jana-Smyslena"
    assert reservation.project_code == "2026-123456/RG-1-01"
    assert reservation.experiment_plan == "SEM imaging of thin films"


def test_get_reservations_covers_monday_0000_to_sunday_2359(api: FakeBookingApi) -> None:
    api.reservations = [
        service_appointment("before", SEM, "2026-10-18T23:00:00", "2026-10-19T01:00:00"),
        service_appointment("first", SEM, "2026-10-19T00:00:00", "2026-10-19T02:00:00"),
        service_appointment("last", SEM, "2026-10-25T23:59:00", "2026-10-26T01:00:00"),
        service_appointment("after", SEM, "2026-10-26T00:00:00", "2026-10-26T02:00:00"),
    ]

    ids = [r.id for r in ApiBookingSource(api).get_reservations(SEM, WEEK)]

    assert ids == ["first", "last"]


def test_get_reservations_reads_every_page_of_50(api: FakeBookingApi) -> None:
    # 120 reservations on other instruments come first, so the target one sits on the third page.
    api.reservations = [
        service_appointment(f"other-{i}", LAB, "2026-10-19T08:00:00", "2026-10-19T09:00:00") for i in range(120)
    ] + [service_appointment("mine", SEM, "2026-10-23T08:00:00", "2026-10-23T09:00:00")]
    api.recordings = [recording(f"other-{i}", "2026-10-19T08:00:00", "x") for i in range(120)] + [
        recording("mine", "2026-10-23T08:00:00", "Found on page three")
    ]

    [reservation] = ApiBookingSource(api).get_reservations(SEM, WEEK)

    assert reservation.id == "mine"
    assert reservation.experiment_plan == "Found on page three"


def test_get_reservations_gives_an_empty_plan_when_it_is_missing(api: FakeBookingApi) -> None:
    api.reservations = [
        service_appointment("no-recording", SEM, "2026-10-20T09:00:00", "2026-10-20T10:00:00"),
        service_appointment("null-plan", SEM, "2026-10-21T09:00:00", "2026-10-21T10:00:00"),
    ]
    api.recordings = [recording("null-plan", "2026-10-21T09:00:00", None), recording(None, "2026-10-21T09:00:00", "No session")]

    plans = {r.id: r.experiment_plan for r in ApiBookingSource(api).get_reservations(SEM, WEEK)}

    assert plans == {"no-recording": "", "null-plan": ""}
