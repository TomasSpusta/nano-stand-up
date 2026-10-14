from datetime import date, datetime

import pytest

from nano_standup.booking_api import ApiBookingSource, BookingDataError
from nano_standup.booking_source import InstrumentNotFound
from nano_standup.domain import Guarantor, ReservationStatus, Week

from fake_booking_api import SEM_ID as SEM
from fake_booking_api import FakeBookingApi, guarantor_json, instrument_json, recording, reservation_json, sem_json

LAB = "equip-lab"
WEEK = Week(date(2026, 10, 19))


@pytest.fixture
def api() -> FakeBookingApi:
    api = FakeBookingApi()
    api.instruments[SEM] = sem_json("Hana.Testova@example.org")
    api.instruments[LAB] = instrument_json(LAB, "TEST-LAB", None, "Testovaci laborator", guarantor_json("Ota", "Zkusebni", "ota.zkusebni@example.org"))
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
    api.instruments["equip-x"] = instrument_json("equip-x", "TEST-X", "Test X", "Test X", guarantor_json("Nikdo", "Nikdo", None))

    with pytest.raises(BookingDataError, match="TEST-X"):
        ApiBookingSource(api).get_instrument("equip-x")


def test_get_reservations_returns_the_instrument_week_with_user_project_and_experiment_plan(api: FakeBookingApi) -> None:
    api.reservations = [
        reservation_json("r1", SEM, "2026-10-20T09:00:00", "2026-10-20T11:30:00", subject="2026-123456/RG-1-01", firstname="Jana", lastname="Smyslena"),
        reservation_json("r2", LAB, "2026-10-20T09:00:00", "2026-10-20T10:00:00"),
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
        reservation_json("before", SEM, "2026-10-18T23:00:00", "2026-10-19T01:00:00"),
        reservation_json("first", SEM, "2026-10-19T00:00:00", "2026-10-19T02:00:00"),
        reservation_json("last", SEM, "2026-10-25T23:59:00", "2026-10-26T01:00:00"),
        reservation_json("after", SEM, "2026-10-26T00:00:00", "2026-10-26T02:00:00"),
    ]

    ids = [r.id for r in ApiBookingSource(api).get_reservations(SEM, WEEK)]

    assert ids == ["first", "last"]


def test_get_reservations_reads_every_page_of_50(api: FakeBookingApi) -> None:
    # 120 reservations on other instruments come first, so the target one sits on the third page.
    api.reservations = [
        reservation_json(f"other-{i}", LAB, "2026-10-19T08:00:00", "2026-10-19T09:00:00") for i in range(120)
    ] + [reservation_json("mine", SEM, "2026-10-23T08:00:00", "2026-10-23T09:00:00")]
    api.recordings = [recording(f"other-{i}", "2026-10-19T08:00:00", "x") for i in range(120)] + [
        recording("mine", "2026-10-23T08:00:00", "Found on page three")
    ]

    [reservation] = ApiBookingSource(api).get_reservations(SEM, WEEK)

    assert reservation.id == "mine"
    assert reservation.experiment_plan == "Found on page three"


def test_get_reservations_gives_an_empty_experiment_plan_when_it_is_missing(api: FakeBookingApi) -> None:
    api.reservations = [
        reservation_json("no-recording", SEM, "2026-10-20T09:00:00", "2026-10-20T10:00:00"),
        reservation_json("null-plan", SEM, "2026-10-21T09:00:00", "2026-10-21T10:00:00"),
    ]
    api.recordings = [recording("null-plan", "2026-10-21T09:00:00", None), recording(None, "2026-10-21T09:00:00", "No session")]

    experiment_plans = {r.id: r.experiment_plan for r in ApiBookingSource(api).get_reservations(SEM, WEEK)}

    assert experiment_plans == {"no-recording": "", "null-plan": ""}


def test_get_reservations_keeps_a_filled_experiment_plan_over_an_empty_one(api: FakeBookingApi) -> None:
    api.reservations = [reservation_json("r1", SEM, "2026-10-20T09:00:00", "2026-10-20T10:00:00")]
    api.recordings = [
        recording("r1", "2026-10-20T09:00:00", "SEM imaging of thin films"),
        recording("r1", "2026-10-20T09:30:00", None),
    ]

    [reservation] = ApiBookingSource(api).get_reservations(SEM, WEEK)

    assert reservation.experiment_plan == "SEM imaging of thin films"


def test_get_reservations_refuses_a_time_with_a_time_zone(api: FakeBookingApi) -> None:
    api.reservations = [reservation_json("r1", SEM, "2026-10-20T09:00:00+02:00", "2026-10-20T10:00:00+02:00")]

    with pytest.raises(BookingDataError, match="r1.*time zone"):
        ApiBookingSource(api).get_reservations(SEM, WEEK)


def test_get_reservations_names_the_reservation_when_a_time_is_missing(api: FakeBookingApi) -> None:
    api.reservations = [reservation_json("r1", SEM, "2026-10-20T09:00:00", "2026-10-20T10:00:00")]
    del api.reservations[0]["scheduledend"]

    with pytest.raises(BookingDataError, match="r1.*scheduledend"):
        ApiBookingSource(api).get_reservations(SEM, WEEK)


def test_get_instrument_names_the_instrument_when_its_id_is_missing(api: FakeBookingApi) -> None:
    del api.instruments[SEM]["id"]

    with pytest.raises(BookingDataError, match=SEM):
        ApiBookingSource(api).get_instrument(SEM)


def test_get_reservations_reads_status_note_and_private_note(api: FakeBookingApi) -> None:
    api.reservations = [
        reservation_json("booked", SEM, "2026-10-20T09:00:00", "2026-10-20T10:00:00", status=4, note=None, private_note=None),
        reservation_json("tentative", SEM, "2026-10-21T09:00:00", "2026-10-21T10:00:00", status=1, note="Bring own holder"),
        reservation_json("cancelled", SEM, "2026-10-22T09:00:00", "2026-10-22T10:00:00", status=9, private_note="Paid by the group"),
    ]

    reservations = {r.id: r for r in ApiBookingSource(api).get_reservations(SEM, WEEK)}

    assert {i: r.status for i, r in reservations.items()} == {
        "booked": ReservationStatus.BOOKED,
        "tentative": ReservationStatus.TENTATIVE,
        "cancelled": ReservationStatus.CANCELLED,
    }
    assert (reservations["booked"].note, reservations["booked"].private_note) == ("", "")
    assert reservations["tentative"].note == "Bring own holder"
    assert reservations["cancelled"].private_note == "Paid by the group"
