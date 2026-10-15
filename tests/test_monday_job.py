from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from nano_standup.booking_source import FakeBookingSource
from nano_standup.domain import Certificate, Guarantor, Instrument, Reservation, ReservationStatus
from nano_standup.mail import InMemoryMailSender, Message
from nano_standup.monday_job import run_monday_job
from nano_standup.subscriptions import SubscriptionStore

from helpers import (
    GUARANTOR,
    GUARANTOR_USER_ID,
    HEADS_UP_SETTINGS,
    INSTRUMENT,
    OLD_CERTIFICATE,
    heads_up_text,
    reservation,
    visible_text,
)

# Monday 19 October 2026, 08:00 in Prague.
MONDAY_8AM = datetime(2026, 10, 19, 6, 0, tzinfo=UTC)
OTHER = Instrument(
    id="instr-2",
    name="Test Chemical Laboratory",
    alias="TEST-LAB",
    guarantor=Guarantor(name="Ota Zkusebni", email="ota.zkusebni@example.org", user_id="user-Ota Zkusebni"),
)
JANA = "user-Jana Smyslena"
KAREL = "user-Karel Neexistujici"
CERTIFIED_IN_OCTOBER = Certificate(valid_from=date(2026, 10, 1), valid_to=date(2027, 10, 1))


def _run(source: FakeBookingSource, store: SubscriptionStore, mail_sender: InMemoryMailSender) -> None:
    """Runs the Monday job on Monday 08:00, with the default heads-up settings."""
    run_monday_job(
        source,
        store,
        mail_sender,
        clock=lambda: MONDAY_8AM,
        from_address="standup@example.org",
        heads_up_settings=HEADS_UP_SETTINGS,
    )


def _earlier_reservations(user_name: str) -> list[Reservation]:
    """3 reservations in January, after OLD_CERTIFICATE starts, so the user is not a new user."""
    return [reservation(f"2026-01-0{day}T09:00", f"2026-01-0{day}T10:00", user_name=user_name) for day in (5, 6, 7)]


def _brief_for(tmp_path: Path, reservations: list[Reservation], certificates: dict[str, list[Certificate]]) -> Message:
    """The one brief the Monday job sends for the test instrument. `certificates` by user id."""
    source = FakeBookingSource([INSTRUMENT], {INSTRUMENT.id: reservations}, {INSTRUMENT.id: certificates})
    store = SubscriptionStore(tmp_path / "standup.sqlite3")
    store.add_active(INSTRUMENT.id, GUARANTOR.email, MONDAY_8AM)
    mail_sender = InMemoryMailSender()
    _run(source, store, mail_sender)
    [message] = mail_sender.sent
    return message


def test_sends_one_brief_per_active_subscription_to_the_subscriber(tmp_path: Path) -> None:
    source = FakeBookingSource(
        instruments=[INSTRUMENT, OTHER],
        reservations={
            INSTRUMENT.id: [
                reservation("2026-10-20T09:00", "2026-10-20T11:00", user_name="Jana Smyslena"),
                # The week before: not in this brief.
                reservation("2026-10-16T09:00", "2026-10-16T11:00", user_name="Karel Neexistujici"),
                # Runs in from the week before: in this brief.
                reservation("2026-10-18T22:00", "2026-10-19T06:00", user_name="Eva Testova"),
            ],
        },
    )
    store = SubscriptionStore(tmp_path / "standup.sqlite3")
    store.add_active(INSTRUMENT.id, "hana.testova@example.org", MONDAY_8AM)
    store.add_active(OTHER.id, "ota.zkusebni@example.org", MONDAY_8AM)
    mail_sender = InMemoryMailSender()

    _run(source, store, mail_sender)

    sent = sorted(mail_sender.sent, key=lambda m: m.to)
    assert [(m.to, m.subject) for m in sent] == [
        ("hana.testova@example.org", "Brief: TEST-SEM - week 19.-25. 10. 2026"),
        ("ota.zkusebni@example.org", "Brief: TEST-LAB - week 19.-25. 10. 2026"),
    ]
    assert all(m.from_address == "standup@example.org" for m in sent)
    sem_text = visible_text(sent[0].html)
    assert "Jana Smyslena" in sem_text
    assert "Karel Neexistujici" not in sem_text
    assert "from Sun 22:00 to 06:00" in sem_text
    assert "Built on Monday 19. 10. 2026 at 08:00." in sem_text
    assert "No reservations this week." in visible_text(sent[1].html)
    assert "Jana Smyslena" in sent[0].text
    assert "No reservations this week." in sent[1].text


def test_sends_nothing_without_subscriptions(tmp_path: Path) -> None:
    mail_sender = InMemoryMailSender()

    _run(FakeBookingSource(instruments=[INSTRUMENT]), SubscriptionStore(tmp_path / "standup.sqlite3"), mail_sender)

    assert mail_sender.sent == []


def test_a_brief_shows_cancelled_and_overnight_reservations_in_both_versions(tmp_path: Path) -> None:
    reservations = [
        reservation("2026-10-20T15:00", "2026-10-21T13:00", user_name="Jana Smyslena"),
        reservation(
            "2026-10-22T09:00", "2026-10-22T10:00", user_name="Karel Neexistujici", status=ReservationStatus.CANCELLED
        ),
        *_earlier_reservations("Jana Smyslena"),
    ]

    message = _brief_for(tmp_path, reservations, {JANA: [OLD_CERTIFICATE]})

    html_text = visible_text(message.html)
    assert "1 reservation 22 h booked 1 user 0 without an experiment plan 1 cancelled" in html_text
    assert "15:00 to Wed 13:00 22 h Jana Smyslena" in html_text
    assert "09:00 to 10:00 1 h Cancelled Karel Neexistujici" in html_text
    assert "15:00 to Wed 13:00 (22 h)" in message.text
    assert "[Cancelled] 09:00 to 10:00 (1 h)" in message.text


def test_a_brief_points_out_heads_ups_in_both_versions(tmp_path: Path) -> None:
    source = FakeBookingSource(
        instruments=[INSTRUMENT],
        reservations={
            INSTRUMENT.id: [
                reservation("2026-10-20T09:00", "2026-10-20T11:00", user_name="Karel Neexistujici"),
                reservation("2026-10-21T18:00", "2026-10-21T21:00", user_name="Jana Smyslena", experiment_plan=""),
                *_earlier_reservations("Karel Neexistujici"),
                *_earlier_reservations("Jana Smyslena"),
            ],
        },
        certificates={INSTRUMENT.id: {JANA: [OLD_CERTIFICATE], KAREL: [OLD_CERTIFICATE]}},
    )
    store = SubscriptionStore(tmp_path / "standup.sqlite3")
    store.add_active(INSTRUMENT.id, "hana.testova@example.org", MONDAY_8AM)
    mail_sender = InMemoryMailSender()

    _run(source, store, mail_sender)

    [message] = mail_sender.sent
    assert heads_up_text(message.text, message.html) == (
        "No experiment plan Wed 21. 10. 18:00 to 21:00, Jana Smyslena "
        "Outside working hours (07:00 to 19:00, Monday to Friday) Wed 21. 10. 18:00 to 21:00, Jana Smyslena"
    )


@pytest.mark.parametrize(("earlier", "number"), [(0, 1), (1, 2), (2, 3)])
def test_a_user_with_fewer_than_3_reservations_since_the_certificate_is_a_new_user(
    tmp_path: Path, earlier: int, number: int
) -> None:
    reservations = [
        # Before the certificate, cancelled, or on another day of the user: not counted.
        reservation("2026-09-21T09:00", "2026-09-21T10:00", user_name="Jana Smyslena"),
        reservation(
            "2026-10-02T09:00", "2026-10-02T10:00", user_name="Jana Smyslena", status=ReservationStatus.CANCELLED
        ),
        *[
            reservation(f"2026-10-0{5 + day}T09:00", f"2026-10-0{5 + day}T10:00", user_name="Jana Smyslena")
            for day in range(earlier)
        ],
        reservation("2026-10-20T09:00", "2026-10-20T10:00", user_name="Jana Smyslena"),
    ]

    message = _brief_for(tmp_path, reservations, {JANA: [CERTIFIED_IN_OCTOBER]})

    assert heads_up_text(message.text, message.html) == (
        f"New user Tue 20. 10. 09:00 to 10:00, Jana Smyslena (no. {number})"
    )
    assert f"[New user, no. {number}] 09:00 to 10:00 (1 h)" in message.text
    assert f"New user, no. {number} Jana Smyslena" in visible_text(message.html)


def test_the_fourth_reservation_since_the_certificate_is_not_by_a_new_user(tmp_path: Path) -> None:
    reservations = [
        reservation("2026-10-05T09:00", "2026-10-05T10:00", user_name="Jana Smyslena"),
        reservation("2026-10-06T09:00", "2026-10-06T10:00", user_name="Jana Smyslena"),
        # The third and the fourth are in this week.
        reservation("2026-10-20T09:00", "2026-10-20T10:00", user_name="Jana Smyslena"),
        reservation("2026-10-21T09:00", "2026-10-21T10:00", user_name="Jana Smyslena"),
    ]

    message = _brief_for(tmp_path, reservations, {JANA: [CERTIFIED_IN_OCTOBER]})

    assert heads_up_text(message.text, message.html) == "New user Tue 20. 10. 09:00 to 10:00, Jana Smyslena (no. 3)"
    assert "no. 4" not in message.text


def test_a_user_with_several_certificates_counts_from_the_earliest_valid_from(tmp_path: Path) -> None:
    reservations = [
        reservation("2026-02-02T09:00", "2026-02-02T10:00", user_name="Jana Smyslena"),
        reservation("2026-03-02T09:00", "2026-03-02T10:00", user_name="Jana Smyslena"),
        reservation("2026-10-20T09:00", "2026-10-20T10:00", user_name="Jana Smyslena"),
    ]
    certificates = [
        # The newer one first, so the order the booking system gives does not decide.
        CERTIFIED_IN_OCTOBER,
        Certificate(valid_from=date(2026, 1, 15), valid_to=date(2026, 12, 31)),
    ]

    message = _brief_for(tmp_path, reservations, {JANA: certificates})

    assert heads_up_text(message.text, message.html) == "New user Tue 20. 10. 09:00 to 10:00, Jana Smyslena (no. 3)"


def test_a_user_whose_certificates_have_expired_has_no_valid_certificate(tmp_path: Path) -> None:
    reservations = [reservation("2026-10-20T09:00", "2026-10-20T10:00", user_name="Jana Smyslena")]
    certificates = [
        Certificate(valid_from=date(2023, 1, 1), valid_to=date(2026, 5, 31)),
        Certificate(valid_from=date(2022, 1, 1), valid_to=date(2024, 12, 31)),
    ]
    # Many reservations since the earliest certificate, so she is not a new user.
    reservations += [
        reservation(f"2023-0{month}-01T09:00", f"2023-0{month}-01T10:00", user_name="Jana Smyslena")
        for month in (2, 3, 4)
    ]

    message = _brief_for(tmp_path, reservations, {JANA: certificates})

    assert heads_up_text(message.text, message.html) == (
        "No valid certificate Tue 20. 10. 09:00 to 10:00, Jana Smyslena (expired 31. 5. 2026)"
    )
    assert "[No valid certificate] 09:00 to 10:00 (1 h)" in message.text
    assert "No valid certificate Jana Smyslena" in visible_text(message.html)


def test_a_user_without_a_certificate_has_no_valid_certificate_and_is_not_a_new_user(tmp_path: Path) -> None:
    reservations = [reservation("2026-10-20T09:00", "2026-10-20T10:00", user_name="Jana Smyslena")]

    message = _brief_for(tmp_path, reservations, {})

    assert heads_up_text(message.text, message.html) == (
        "No valid certificate Tue 20. 10. 09:00 to 10:00, Jana Smyslena (missing)"
    )


def test_a_certificate_valid_on_the_reservation_day_is_enough(tmp_path: Path) -> None:
    reservations = [
        reservation("2026-01-05T09:00", "2026-01-05T10:00", user_name="Jana Smyslena"),
        reservation("2026-01-06T09:00", "2026-01-06T10:00", user_name="Jana Smyslena"),
        reservation("2026-01-07T09:00", "2026-01-07T10:00", user_name="Jana Smyslena"),
        reservation("2026-10-20T18:00", "2026-10-20T19:00", user_name="Jana Smyslena"),
    ]
    ends_that_day = Certificate(valid_from=date(2026, 1, 1), valid_to=date(2026, 10, 20))

    message = _brief_for(tmp_path, reservations, {JANA: [ends_that_day]})

    assert "HEADS-UP" not in message.text


@pytest.mark.parametrize(
    "certificates",
    [[], [Certificate(valid_from=date(2020, 1, 1), valid_to=date(2026, 5, 31))]],
    ids=["missing", "expired"],
)
def test_the_guarantor_own_reservations_never_lack_a_certificate(
    tmp_path: Path, certificates: list[Certificate]
) -> None:
    reservations = [reservation("2026-10-20T09:00", "2026-10-20T10:00", user_name=GUARANTOR.name)]
    reservations += _earlier_reservations(GUARANTOR.name)

    message = _brief_for(tmp_path, reservations, {GUARANTOR_USER_ID: certificates})

    assert "HEADS-UP" not in message.text
    assert "No valid certificate" not in visible_text(message.html)
