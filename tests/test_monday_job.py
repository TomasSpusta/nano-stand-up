from datetime import UTC, datetime
from pathlib import Path

from nano_standup.booking_source import FakeBookingSource
from nano_standup.domain import Guarantor, Instrument, ReservationStatus
from nano_standup.mail import InMemoryMailSender
from nano_standup.monday_job import run_monday_job
from nano_standup.subscriptions import SubscriptionStore

from helpers import INSTRUMENT, reservation, visible_text

# Monday 19 October 2026, 08:00 in Prague.
MONDAY_8AM = datetime(2026, 10, 19, 6, 0, tzinfo=UTC)
OTHER = Instrument(
    id="instr-2",
    name="Test Chemical Laboratory",
    alias="TEST-LAB",
    guarantor=Guarantor(name="Ota Zkusebni", email="ota.zkusebni@example.org"),
)


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

    run_monday_job(source, store, mail_sender, clock=lambda: MONDAY_8AM, from_address="standup@example.org")

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

    run_monday_job(
        FakeBookingSource(instruments=[INSTRUMENT]),
        SubscriptionStore(tmp_path / "standup.sqlite3"),
        mail_sender,
        clock=lambda: MONDAY_8AM,
        from_address="standup@example.org",
    )

    assert mail_sender.sent == []


def test_a_brief_shows_cancelled_and_overnight_reservations_in_both_versions(tmp_path: Path) -> None:
    source = FakeBookingSource(
        instruments=[INSTRUMENT],
        reservations={
            INSTRUMENT.id: [
                reservation("2026-10-20T15:00", "2026-10-21T13:00", user_name="Jana Smyslena"),
                reservation(
                    "2026-10-22T09:00", "2026-10-22T10:00", user_name="Karel Neexistujici", status=ReservationStatus.CANCELLED
                ),
            ],
        },
    )
    store = SubscriptionStore(tmp_path / "standup.sqlite3")
    store.add_active(INSTRUMENT.id, "hana.testova@example.org", MONDAY_8AM)
    mail_sender = InMemoryMailSender()

    run_monday_job(source, store, mail_sender, clock=lambda: MONDAY_8AM, from_address="standup@example.org")

    [message] = mail_sender.sent
    html_text = visible_text(message.html)
    assert "1 reservation 22 h booked 1 user 0 without an experiment plan 1 cancelled" in html_text
    assert "15:00 to Wed 13:00 22 h Jana Smyslena" in html_text
    assert "09:00 to 10:00 1 h Cancelled Karel Neexistujici" in html_text
    assert "15:00 to Wed 13:00 (22 h)" in message.text
    assert "[Cancelled] 09:00 to 10:00 (1 h)" in message.text
