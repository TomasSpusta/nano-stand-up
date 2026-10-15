from datetime import date, datetime, timedelta
from typing import Any

import pytest

from nano_standup.booking_api import (
    CANCELLED_STATE,
    CANCELLED_STATUS,
    REQUESTED_STATUS,
    RESERVED_STATUS,
    ApiBookingSource,
    BookingAccessDenied,
    BookingDataError,
)
from nano_standup.booking_source import InstrumentNotFound
from nano_standup.domain import Certificate, Guarantor, ReservationStatus, UserFacts, Week

from fake_booking_api import SEM_ID as SEM
from fake_booking_api import (
    FakeBookingApi,
    certificate_json,
    contact_json,
    guarantor_json,
    instrument_json,
    recording,
    reservation_json,
    sem_json,
    session_json,
)

LAB = "equip-lab"
WEEK = Week(date(2026, 10, 19))
# A statuscode the booking system does not use for reservations.
UNKNOWN_STATUS = 7


@pytest.fixture
def api() -> FakeBookingApi:
    api = FakeBookingApi()
    api.instruments[SEM] = sem_json("Hana.Testova@example.org")
    api.instruments[LAB] = instrument_json(
        LAB, "TEST-LAB", None, "Testovaci laborator", guarantor_json("Ota", "Zkusebni", "ota.zkusebni@example.org")
    )
    return api


def test_get_instrument_returns_its_names_and_guarantor_first_name_first(api: FakeBookingApi) -> None:
    instrument = ApiBookingSource(api).get_instrument(SEM)

    assert (instrument.id, instrument.name, instrument.alias) == (SEM, "Test Electron Microscope", "TEST-SEM")
    assert instrument.guarantor == Guarantor("Hana Testova", "Hana.Testova@example.org", "contact-Hana-Testova")


def test_get_instrument_uses_the_czech_name_when_there_is_no_english_one(api: FakeBookingApi) -> None:
    assert ApiBookingSource(api).get_instrument(LAB).name == "Testovaci laborator"


def test_get_instrument_raises_instrument_not_found_for_an_unknown_id(api: FakeBookingApi) -> None:
    with pytest.raises(InstrumentNotFound, match="no-such-id"):
        ApiBookingSource(api).get_instrument("no-such-id")


def test_get_instrument_raises_booking_access_denied_when_the_api_key_has_no_access(api: FakeBookingApi) -> None:
    api.forbidden.add(SEM)

    with pytest.raises(BookingAccessDenied, match=f"No access to instrument '{SEM}'.*HTTP 403"):
        ApiBookingSource(api).get_instrument(SEM)


def test_get_instrument_refuses_an_instrument_without_a_guarantor_email(api: FakeBookingApi) -> None:
    api.instruments["equip-x"] = instrument_json(
        "equip-x", "TEST-X", "Test X", "Test X", guarantor_json("Nikdo", "Nikdo", None)
    )

    with pytest.raises(BookingDataError, match="TEST-X"):
        ApiBookingSource(api).get_instrument("equip-x")


def test_get_reservations_returns_the_instrument_week_with_user_project_and_experiment_plan(
    api: FakeBookingApi,
) -> None:
    api.reservations = [
        reservation_json(
            "r1",
            SEM,
            "2026-10-20T09:00:00",
            "2026-10-20T11:30:00",
            subject="2026-123456/RG-1-01",
            firstname="Jana",
            lastname="Smyslena",
        ),
        reservation_json("r2", LAB, "2026-10-20T09:00:00", "2026-10-20T10:00:00"),
    ]
    api.recordings = [
        recording("r1", "2026-10-20T09:00:00", "SEM imaging of thin films"),
        recording("r2", "2026-10-20T09:00:00", "Other"),
    ]

    [reservation] = ApiBookingSource(api).get_reservations(SEM, WEEK)

    assert reservation.id == "r1"
    assert (reservation.start, reservation.end) == (datetime(2026, 10, 20, 9, 0), datetime(2026, 10, 20, 11, 30))
    assert reservation.user_name == "Jana Smyslena"
    assert reservation.user_id == "contact-Jana-Smyslena"
    assert reservation.project_code == "2026-123456/RG-1-01"
    assert reservation.experiment_plan == "SEM imaging of thin films"


def test_get_reservations_covers_monday_0000_to_sunday_2359(api: FakeBookingApi) -> None:
    api.reservations = [
        reservation_json("ends-at-monday", SEM, "2026-10-18T22:00:00", "2026-10-19T00:00:00"),
        reservation_json("runs-in", SEM, "2026-10-18T23:00:00", "2026-10-19T01:00:00"),
        reservation_json("first", SEM, "2026-10-19T00:00:00", "2026-10-19T02:00:00"),
        reservation_json("last", SEM, "2026-10-25T23:59:00", "2026-10-26T01:00:00"),
        reservation_json("after", SEM, "2026-10-26T00:00:00", "2026-10-26T02:00:00"),
    ]

    ids = [r.id for r in ApiBookingSource(api).get_reservations(SEM, WEEK)]

    assert ids == ["runs-in", "first", "last"]


def test_get_reservations_finds_the_experiment_plan_of_a_reservation_that_started_weeks_before(
    api: FakeBookingApi,
) -> None:
    api.reservations = [
        reservation_json("long", SEM, "2026-09-28T09:00:00", "2026-10-20T09:00:00"),
        reservation_json("short", SEM, "2026-10-21T09:00:00", "2026-10-21T10:00:00"),
    ]
    api.recordings = [
        recording("long", "2026-09-28T09:00:00", "Long annealing run"),
        recording("short", "2026-10-21T09:00:00", "SEM imaging"),
    ]

    experiment_plans = {r.id: r.experiment_plan for r in ApiBookingSource(api).get_reservations(SEM, WEEK)}

    assert experiment_plans == {"long": "Long annealing run", "short": "SEM imaging"}


def test_get_reservations_reads_recordings_only_near_each_start(api: FakeBookingApi) -> None:
    api.reservations = [reservation_json("long", SEM, "2024-01-04T09:00:00", "2026-10-20T09:00:00")]
    api.recordings = [recording("long", "2024-01-04T09:00:00", "Long annealing run")]
    # Recordings in between: a read from the earliest start to Sunday would page through them all.
    api.recordings += [recording(f"other-{i}", "2025-06-01T09:00:00", "x") for i in range(120)]

    [reservation] = ApiBookingSource(api).get_reservations(SEM, WEEK)

    assert reservation.experiment_plan == "Long annealing run"
    assert api.recordings_read == 1


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
    api.recordings = [
        recording("null-plan", "2026-10-21T09:00:00", None),
        recording(None, "2026-10-21T09:00:00", "No session"),
    ]

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


def test_get_reservations_names_the_reservation_when_a_time_is_not_a_date(api: FakeBookingApi) -> None:
    api.reservations = [reservation_json("r1", SEM, "2026-10-20T09:00:00", "2026-10-20T10:00:00 or so")]

    with pytest.raises(BookingDataError, match="r1 has a scheduledend that is not a date and time"):
        ApiBookingSource(api).get_reservations(SEM, WEEK)


def test_get_instrument_names_the_instrument_when_its_id_is_missing(api: FakeBookingApi) -> None:
    del api.instruments[SEM]["id"]

    with pytest.raises(BookingDataError, match=SEM):
        ApiBookingSource(api).get_instrument(SEM)


def test_get_reservations_reads_status_note_and_private_note(api: FakeBookingApi) -> None:
    api.reservations = [
        reservation_json(
            "reserved",
            SEM,
            "2026-10-20T09:00:00",
            "2026-10-20T10:00:00",
            statuscode=RESERVED_STATUS,
            note=None,
            private_note=None,
        ),
        reservation_json(
            "requested",
            SEM,
            "2026-10-21T09:00:00",
            "2026-10-21T10:00:00",
            statuscode=REQUESTED_STATUS,
            note="Bring own holder",
        ),
        reservation_json(
            "cancelled",
            SEM,
            "2026-10-22T09:00:00",
            "2026-10-22T10:00:00",
            statuscode=CANCELLED_STATUS,
            private_note="Paid by the group",
        ),
    ]

    reservations = {r.id: r for r in ApiBookingSource(api).get_reservations(SEM, WEEK)}

    assert {i: r.status for i, r in reservations.items()} == {
        "reserved": ReservationStatus.RESERVED,
        "requested": ReservationStatus.REQUESTED,
        "cancelled": ReservationStatus.CANCELLED,
    }
    assert (reservations["reserved"].note, reservations["reserved"].private_note) == ("", "")
    assert reservations["requested"].note == "Bring own holder"
    assert reservations["cancelled"].private_note == "Paid by the group"


def test_get_reservations_treats_a_cancelled_state_as_cancelled_whatever_its_status(api: FakeBookingApi) -> None:
    api.reservations = [
        reservation_json("r1", SEM, "2026-10-20T09:00:00", "2026-10-20T10:00:00", statuscode=RESERVED_STATUS)
    ]
    api.reservations[0]["statecode"] = CANCELLED_STATE

    [reservation] = ApiBookingSource(api).get_reservations(SEM, WEEK)

    assert reservation.status is ReservationStatus.CANCELLED


def test_get_reservations_logs_an_unknown_status_and_shows_it_as_reserved(
    api: FakeBookingApi, caplog: pytest.LogCaptureFixture
) -> None:
    api.reservations = [
        reservation_json("r1", SEM, "2026-10-20T09:00:00", "2026-10-20T10:00:00", statuscode=UNKNOWN_STATUS)
    ]

    [reservation] = ApiBookingSource(api).get_reservations(SEM, WEEK)

    assert reservation.status is ReservationStatus.RESERVED
    assert f"Reservation r1 has an unknown statuscode {UNKNOWN_STATUS}" in caplog.text


JANA = "contact-Jana-Smyslena"


def _jana_json(
    id: str, instrument_id: str, start: str, statuscode: int = RESERVED_STATUS
) -> dict[str, Any]:  # Any: mirrors the API's JSON.
    """A one-hour reservation by Jana Smyslena."""
    end = (datetime.fromisoformat(start) + timedelta(hours=1)).isoformat()
    return reservation_json(id, instrument_id, start, end, firstname="Jana", lastname="Smyslena", statuscode=statuscode)


def test_get_instrument_gives_the_guarantor_user_id(api: FakeBookingApi) -> None:
    assert ApiBookingSource(api).get_instrument(SEM).guarantor.user_id == "contact-Hana-Testova"


def test_get_instrument_logs_a_guarantor_without_a_contact_and_gives_no_user_id(
    api: FakeBookingApi, caplog: pytest.LogCaptureFixture
) -> None:
    del api.instruments[SEM]["ge_ownerid"]["ge_contactid"]

    instrument = ApiBookingSource(api).get_instrument(SEM)

    assert instrument.guarantor.user_id is None
    assert "TEST-SEM" in caplog.text
    assert "no contact" in caplog.text


def test_get_certificates_reads_them_through_the_user_system_user(api: FakeBookingApi) -> None:
    api.contacts[JANA] = contact_json(JANA, "sysuser-jana")
    api.certificates[("sysuser-jana", SEM)] = [
        certificate_json("2024-04-23T00:00:00", "2027-04-23T00:00:00"),
        certificate_json("2026-02-05T00:00:00", None),
    ]
    api.certificates[("sysuser-jana", LAB)] = [certificate_json("2020-01-01T00:00:00", None)]

    certificates = ApiBookingSource(api).get_certificates(SEM, JANA)

    assert certificates == [
        Certificate(valid_from=date(2024, 4, 23), valid_to=date(2027, 4, 23)),
        Certificate(valid_from=date(2026, 2, 5), valid_to=None),
    ]


def test_get_certificates_reads_them_all_in_one_request_because_the_api_ignores_paging(
    api: FakeBookingApi,
) -> None:
    api.contacts[JANA] = contact_json(JANA, "sysuser-jana")
    api.certificates[("sysuser-jana", SEM)] = [
        certificate_json(f"20{10 + i // 12}-{1 + i % 12:02}-01T00:00:00", None) for i in range(60)
    ]

    assert len(ApiBookingSource(api).get_certificates(SEM, JANA)) == 60


def test_a_user_without_a_system_user_has_no_certificates(api: FakeBookingApi) -> None:
    api.contacts[JANA] = contact_json(JANA, None)

    assert ApiBookingSource(api).get_certificates(SEM, JANA) == []


def test_a_certificate_without_a_valid_from_date_is_logged_and_left_out(
    api: FakeBookingApi, caplog: pytest.LogCaptureFixture
) -> None:
    api.contacts[JANA] = contact_json(JANA, "sysuser-jana")
    api.certificates[("sysuser-jana", SEM)] = [certificate_json(None, "2027-04-23T00:00:00")]

    assert ApiBookingSource(api).get_certificates(SEM, JANA) == []
    assert f"A certificate of user {JANA} for instrument {SEM} has no valid-from date" in caplog.text


def test_a_certificate_date_that_is_not_a_date_is_named(api: FakeBookingApi) -> None:
    api.contacts[JANA] = contact_json(JANA, "sysuser-jana")
    api.certificates[("sysuser-jana", SEM)] = [certificate_json("2024-04-23T00:00:00", "soon")]

    with pytest.raises(BookingDataError, match="ge_platnostdo that is not a date and time: 'soon'"):
        ApiBookingSource(api).get_certificates(SEM, JANA)


def test_get_reservation_starts_reads_the_user_sessions_on_the_instrument_without_cancelled_ones(
    api: FakeBookingApi,
) -> None:
    reservations = [
        _jana_json("old", SEM, "2025-03-11T10:30:00"),
        _jana_json("this-week", SEM, "2026-10-20T09:00:00"),
        _jana_json("requested", SEM, "2026-10-21T09:00:00", statuscode=REQUESTED_STATUS),
        _jana_json("cancelled", SEM, "2026-10-22T09:00:00", statuscode=CANCELLED_STATUS),
        _jana_json("other-instrument", LAB, "2026-10-23T09:00:00"),
        reservation_json("other-user", SEM, "2026-10-23T09:00:00", "2026-10-23T10:00:00"),
    ]
    api.sessions = [session_json(r, LAB if r["id"] == "other-instrument" else SEM) for r in reservations]
    # A reservation with two sessions counts once.
    api.sessions.append({**api.sessions[0], "id": "session-old-2"})

    starts = ApiBookingSource(api).get_reservation_starts(SEM, JANA)

    assert sorted(starts) == [datetime(2025, 3, 11, 10, 30), datetime(2026, 10, 20, 9, 0), datetime(2026, 10, 21, 9, 0)]


def test_get_reservation_starts_leaves_out_a_session_without_a_reservation(api: FakeBookingApi) -> None:
    booked = session_json(_jana_json("booked", SEM, "2026-10-20T09:00:00"), SEM)
    api.sessions = [booked, {**booked, "id": "session-not-booked", "ge_serviceappointmentid": None}]

    assert ApiBookingSource(api).get_reservation_starts(SEM, JANA) == [datetime(2026, 10, 20, 9, 0)]


def test_get_user_facts_gives_the_certificates_and_the_reservation_starts(api: FakeBookingApi) -> None:
    api.contacts[JANA] = contact_json(JANA, "sysuser-jana")
    api.certificates[("sysuser-jana", SEM)] = [certificate_json("2024-04-23T00:00:00", None)]
    api.sessions = [session_json(_jana_json("booked", SEM, "2026-10-20T09:00:00"), SEM)]

    assert ApiBookingSource(api).get_user_facts(SEM, JANA) == UserFacts(
        certificates=[Certificate(valid_from=date(2024, 4, 23), valid_to=None)],
        reservation_starts=[datetime(2026, 10, 20, 9, 0)],
    )


def test_get_reservation_starts_reads_every_page_of_50(api: FakeBookingApi) -> None:
    api.sessions = [session_json(_jana_json(f"r{i}", SEM, f"2025-01-01T09:{i:02}:00"), SEM) for i in range(60)]

    assert len(ApiBookingSource(api).get_reservation_starts(SEM, JANA)) == 60
