from datetime import UTC, datetime, timedelta
from pathlib import Path

from nano_standup.booking_source import FakeBookingSource
from nano_standup.domain import Guarantor, Instrument, ReservationStatus
from nano_standup.mail import InMemoryMailSender
from nano_standup.monday_job import MondayJobResult, run_monday_job
from nano_standup.sent_briefs import SentBriefStore
from nano_standup.subscriptions import SubscriptionStore

from helpers import INSTRUMENT, WORKING_HOURS, heads_up_text, reservation, visible_text

# Monday 19 October 2026, 08:00 in Prague.
MONDAY_8AM = datetime(2026, 10, 19, 6, 0, tzinfo=UTC)
OTHER = Instrument(
    id="instr-2",
    name="Test Chemical Laboratory",
    alias="TEST-LAB",
    guarantor=Guarantor(name="Ota Zkusebni", email="ota.zkusebni@example.org"),
)


def _run(
    source: FakeBookingSource, database: Path, mail_sender: InMemoryMailSender, now: datetime = MONDAY_8AM
) -> MondayJobResult:
    """Runs the Monday job at `now` (Monday 08:00 unless given), with the default working hours."""
    return run_monday_job(
        source,
        SubscriptionStore(database),
        SentBriefStore(database),
        mail_sender,
        clock=lambda: now,
        from_address="standup@example.org",
        working_hours=WORKING_HOURS,
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

    _run(source, tmp_path / "standup.sqlite3", mail_sender)

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

    _run(FakeBookingSource(instruments=[INSTRUMENT]), tmp_path / "standup.sqlite3", mail_sender)

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

    _run(source, tmp_path / "standup.sqlite3", mail_sender)

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

    _run(source, tmp_path / "standup.sqlite3", mail_sender)

    [message] = mail_sender.sent
    assert heads_up_text(message.text, message.html) == (
        "No experiment plan Wed 21. 10. 18:00 to 21:00, Jana Smyslena "
        "Outside working hours (07:00 to 19:00, Monday to Friday) Wed 21. 10. 18:00 to 21:00, Jana Smyslena"
    )


def test_a_re_run_skips_the_briefs_already_sent_this_week(tmp_path: Path) -> None:
    database = tmp_path / "standup.sqlite3"
    store = SubscriptionStore(database)
    store.add_active(INSTRUMENT.id, "hana.testova@example.org", MONDAY_8AM)
    source = FakeBookingSource(instruments=[INSTRUMENT, OTHER])
    first_run = InMemoryMailSender()
    _run(source, database, first_run)
    # A new subscription between the runs gets its brief on the re-run.
    store.add_active(OTHER.id, "ota.zkusebni@example.org", MONDAY_8AM)
    re_run = InMemoryMailSender()

    result = _run(source, database, re_run, now=MONDAY_8AM + timedelta(hours=3))

    assert [m.to for m in first_run.sent] == ["hana.testova@example.org"]
    assert [m.to for m in re_run.sent] == ["ota.zkusebni@example.org"]
    assert (result.sent, result.already_sent) == (1, 1)


def test_the_next_week_sends_the_brief_again(tmp_path: Path) -> None:
    database = tmp_path / "standup.sqlite3"
    SubscriptionStore(database).add_active(INSTRUMENT.id, "hana.testova@example.org", MONDAY_8AM)
    source = FakeBookingSource(instruments=[INSTRUMENT])
    mail_sender = InMemoryMailSender()

    _run(source, database, mail_sender)
    _run(source, database, mail_sender, now=MONDAY_8AM + timedelta(days=7))

    assert [m.subject for m in mail_sender.sent] == [
        "Brief: TEST-SEM - week 19.-25. 10. 2026",
        "Brief: TEST-SEM - week 26. 10.-1. 11. 2026",
    ]
