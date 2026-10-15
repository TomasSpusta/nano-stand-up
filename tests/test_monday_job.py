import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from nano_standup.booking_source import FakeBookingSource
from nano_standup.domain import Guarantor, Instrument, ReservationStatus
from nano_standup.mail import InMemoryMailSender
from nano_standup.monday_job import run_monday_job
from nano_standup.subscriptions import Subscription, SubscriptionStore, UnsubscribeOutcome

from helpers import INSTRUMENT, WORKING_HOURS, heads_up_text, reservation, visible_text

# Monday 19 October 2026, 08:00 in Prague.
MONDAY_8AM = datetime(2026, 10, 19, 6, 0, tzinfo=UTC)
OTHER = Instrument(
    id="instr-2",
    name="Test Chemical Laboratory",
    alias="TEST-LAB",
    guarantor=Guarantor(name="Ota Zkusebni", email="ota.zkusebni@example.org"),
)
UNSUBSCRIBE_LINK_PATTERN = re.compile(r"https://standup\.example\.org/unsubscribe\?token=([A-Za-z0-9_-]+)")


def _run(
    source: FakeBookingSource, store: SubscriptionStore, mail_sender: InMemoryMailSender, now: datetime = MONDAY_8AM
) -> None:
    """Runs the Monday job, by default on Monday 08:00, with the default working hours."""
    run_monday_job(
        source,
        store,
        mail_sender,
        clock=lambda: now,
        from_address="standup@example.org",
        working_hours=WORKING_HOURS,
        unsubscribe_url=lambda token: f"https://standup.example.org/unsubscribe?token={token}",
    )


def _unsubscribe_token(text: str) -> str:
    """The token of the one unsubscribe link in a brief's plain-text version."""
    [token] = UNSUBSCRIBE_LINK_PATTERN.findall(text)
    return str(token)


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
    source = FakeBookingSource(
        instruments=[INSTRUMENT],
        reservations={
            INSTRUMENT.id: [
                reservation("2026-10-20T15:00", "2026-10-21T13:00", user_name="Jana Smyslena"),
                reservation(
                    "2026-10-22T09:00",
                    "2026-10-22T10:00",
                    user_name="Karel Neexistujici",
                    status=ReservationStatus.CANCELLED,
                ),
            ],
        },
    )
    store = SubscriptionStore(tmp_path / "standup.sqlite3")
    store.add_active(INSTRUMENT.id, "hana.testova@example.org", MONDAY_8AM)
    mail_sender = InMemoryMailSender()

    _run(source, store, mail_sender)

    [message] = mail_sender.sent
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
            ],
        },
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


def test_each_brief_has_its_own_unsubscribe_link_for_its_subscription(tmp_path: Path) -> None:
    source = FakeBookingSource(instruments=[INSTRUMENT, OTHER])
    store = SubscriptionStore(tmp_path / "standup.sqlite3")
    store.add_active(INSTRUMENT.id, "hana.testova@example.org", MONDAY_8AM)
    store.add_active(OTHER.id, "hana.testova@example.org", MONDAY_8AM)
    mail_sender = InMemoryMailSender()

    _run(source, store, mail_sender)

    sem, lab = mail_sender.sent
    sem_token = _unsubscribe_token(sem.text)
    assert sem_token != _unsubscribe_token(lab.text)
    assert f"https://standup.example.org/unsubscribe?token={sem_token}" in sem.html
    # Mail apps show their own unsubscribe button for it.
    assert sem.unsubscribe_link == f"https://standup.example.org/unsubscribe?token={sem_token}"
    assert store.unsubscribe(sem_token, MONDAY_8AM).subscription == Subscription(
        INSTRUMENT.id, "hana.testova@example.org"
    )


def test_the_next_monday_job_sends_nothing_for_an_unsubscribed_subscription(tmp_path: Path) -> None:
    source = FakeBookingSource(instruments=[INSTRUMENT, OTHER])
    store = SubscriptionStore(tmp_path / "standup.sqlite3")
    store.add_active(INSTRUMENT.id, "hana.testova@example.org", MONDAY_8AM)
    store.add_active(OTHER.id, "hana.testova@example.org", MONDAY_8AM)
    first_week = InMemoryMailSender()
    _run(source, store, first_week)
    sem = next(m for m in first_week.sent if "TEST-SEM" in m.subject)
    assert store.unsubscribe(_unsubscribe_token(sem.text), MONDAY_8AM).outcome is UnsubscribeOutcome.UNSUBSCRIBED

    next_week = InMemoryMailSender()
    _run(source, store, next_week, now=MONDAY_8AM + timedelta(weeks=1))

    assert [(m.to, m.subject) for m in next_week.sent] == [
        ("hana.testova@example.org", "Brief: TEST-LAB - week 26. 10.-1. 11. 2026"),
    ]
