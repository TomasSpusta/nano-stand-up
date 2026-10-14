from datetime import UTC, datetime
from pathlib import Path

from nano_standup.booking_source import FakeBookingSource
from nano_standup.domain import Guarantor, Instrument
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
            ],
        },
    )
    store = SubscriptionStore(tmp_path / "standup.sqlite3")
    store.add_active(INSTRUMENT.id, "hana.testova@example.org", MONDAY_8AM)
    store.add_active(OTHER.id, "ota.zkusebni@example.org", MONDAY_8AM)
    sender = InMemoryMailSender()

    run_monday_job(source, store, sender, clock=lambda: MONDAY_8AM, mail_from="standup@example.org")

    sent = sorted(sender.sent, key=lambda m: m.to)
    assert [(m.to, m.subject) for m in sent] == [
        ("hana.testova@example.org", "Brief: TEST-SEM - week 19.-25. 10. 2026"),
        ("ota.zkusebni@example.org", "Brief: TEST-LAB - week 19.-25. 10. 2026"),
    ]
    assert all(m.sender == "standup@example.org" for m in sent)
    sem_text = visible_text(sent[0].html)
    assert "Jana Smyslena" in sem_text
    assert "Karel Neexistujici" not in sem_text
    assert "Built on Monday 19. 10. 2026 at 08:00." in sem_text
    assert "0 reservations" in visible_text(sent[1].html)


def test_sends_nothing_without_subscriptions(tmp_path: Path) -> None:
    sender = InMemoryMailSender()

    run_monday_job(
        FakeBookingSource(instruments=[INSTRUMENT]),
        SubscriptionStore(tmp_path / "standup.sqlite3"),
        sender,
        clock=lambda: MONDAY_8AM,
        mail_from="standup@example.org",
    )

    assert sender.sent == []
