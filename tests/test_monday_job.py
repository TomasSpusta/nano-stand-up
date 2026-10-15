import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nano_standup.booking_api import BookingApiError
from nano_standup.booking_source import BookingSource, FakeBookingSource
from nano_standup.domain import Guarantor, Instrument, Reservation, ReservationStatus, Week
from nano_standup.mail import InMemoryMailSender, MailSender, Message
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


class FakeTime:
    """A fixed clock that only moves when the job sleeps, so the tests never really wait."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def clock(self) -> datetime:
        return self.now

    def sleep(self, duration: timedelta) -> None:
        self.now += duration


class FailingBookingSource:
    """The fake booking source, but the booking API fails for some instruments until a given time."""

    def __init__(
        self, source: FakeBookingSource, time: FakeTime, failing: set[str], until: datetime | None = None
    ) -> None:
        """`until` None means the failure never ends."""
        self._source = source
        self._time = time
        self._failing = failing
        self._until = until
        # The times the job asked for a failing instrument, for checking the retries.
        self.failed_at: list[datetime] = []

    def _check(self, instrument_id: str) -> None:
        if instrument_id in self._failing and (self._until is None or self._time.now < self._until):
            self.failed_at.append(self._time.now)
            raise BookingApiError(f"/api/equipment/{instrument_id}", 503, "Service Unavailable")

    def get_instrument(self, instrument_id: str) -> Instrument:
        self._check(instrument_id)
        return self._source.get_instrument(instrument_id)

    def get_reservations(self, instrument_id: str, week: Week) -> list[Reservation]:
        self._check(instrument_id)
        return self._source.get_reservations(instrument_id, week)


class FailingMailSender(InMemoryMailSender):
    """Keeps the messages in memory, but the mail server is down until a given time."""

    def __init__(self, time: FakeTime, until: datetime) -> None:
        super().__init__()
        self._time = time
        self._until = until

    def send(self, message: Message) -> None:
        if self._time.now < self._until:
            raise ConnectionRefusedError(111, "Connection refused")
        super().send(message)


def _run(
    source: BookingSource,
    database: Path,
    mail_sender: MailSender,
    time: FakeTime | None = None,
) -> MondayJobResult:
    """Runs the Monday job with the default working hours. The time is Monday 08:00 unless given."""
    time = time or FakeTime(MONDAY_8AM)
    return run_monday_job(
        source,
        SubscriptionStore(database),
        SentBriefStore(database),
        mail_sender,
        clock=time.clock,
        sleep=time.sleep,
        from_address="standup@example.org",
        service_admin_email="admin@example.org",
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

    result = _run(source, database, re_run, FakeTime(MONDAY_8AM + timedelta(hours=3)))

    assert [m.to for m in first_run.sent] == ["hana.testova@example.org"]
    assert [m.to for m in re_run.sent] == ["ota.zkusebni@example.org"]
    assert (result.sent, result.already_sent) == (1, 1)


def test_the_next_week_sends_the_brief_again(tmp_path: Path) -> None:
    database = tmp_path / "standup.sqlite3"
    SubscriptionStore(database).add_active(INSTRUMENT.id, "hana.testova@example.org", MONDAY_8AM)
    source = FakeBookingSource(instruments=[INSTRUMENT])
    mail_sender = InMemoryMailSender()

    _run(source, database, mail_sender)
    _run(source, database, mail_sender, FakeTime(MONDAY_8AM + timedelta(days=7)))

    assert [m.subject for m in mail_sender.sent] == [
        "Brief: TEST-SEM - week 19.-25. 10. 2026",
        "Brief: TEST-SEM - week 26. 10.-1. 11. 2026",
    ]


def _subscribe_both(database: Path) -> None:
    store = SubscriptionStore(database)
    store.add_active(INSTRUMENT.id, "hana.testova@example.org", MONDAY_8AM)
    store.add_active(OTHER.id, "ota.zkusebni@example.org", MONDAY_8AM)


def test_a_booking_api_failure_is_retried_and_the_late_brief_shows_its_build_time(tmp_path: Path) -> None:
    database = tmp_path / "standup.sqlite3"
    _subscribe_both(database)
    time = FakeTime(MONDAY_8AM)
    # The booking API is back at 08:10 Prague time.
    source = FailingBookingSource(
        FakeBookingSource(instruments=[INSTRUMENT, OTHER]),
        time,
        failing={OTHER.id},
        until=MONDAY_8AM + timedelta(minutes=10),
    )
    mail_sender = InMemoryMailSender()

    result = _run(source, database, mail_sender, time)

    assert [m.to for m in mail_sender.sent] == ["hana.testova@example.org", "ota.zkusebni@example.org"]
    assert "Built on Monday 19. 10. 2026 at 08:00." in mail_sender.sent[0].text
    assert "Built on Monday 19. 10. 2026 at 08:15." in mail_sender.sent[1].text
    assert (result.sent, result.failed) == (2, ())


def test_a_mail_server_failure_is_retried(tmp_path: Path) -> None:
    database = tmp_path / "standup.sqlite3"
    _subscribe_both(database)
    time = FakeTime(MONDAY_8AM)
    mail_sender = FailingMailSender(time, until=MONDAY_8AM + timedelta(minutes=20))

    result = _run(FakeBookingSource(instruments=[INSTRUMENT, OTHER]), database, mail_sender, time)

    assert [m.to for m in mail_sender.sent] == ["hana.testova@example.org", "ota.zkusebni@example.org"]
    assert all("at 08:30." in m.text for m in mail_sender.sent)
    assert (result.sent, result.failed) == (2, ())


def test_a_failure_still_there_at_10_alerts_the_service_admin_once(tmp_path: Path) -> None:
    database = tmp_path / "standup.sqlite3"
    _subscribe_both(database)
    time = FakeTime(MONDAY_8AM)
    source = FailingBookingSource(
        FakeBookingSource(instruments=[INSTRUMENT, OTHER]), time, failing={INSTRUMENT.id, OTHER.id}
    )
    mail_sender = InMemoryMailSender()

    result = _run(source, database, mail_sender, time)

    # Tries every 15 minutes from 08:00 up to and including 10:00 Prague time, then stops.
    tries = [MONDAY_8AM + timedelta(minutes=15 * i) for i in range(9)]
    assert source.failed_at == [t for t in tries for _ in (INSTRUMENT, OTHER)]
    [alert] = mail_sender.sent
    assert alert.to == "admin@example.org"
    assert alert.subject == "Nano Stand-up: 2 briefs not sent for the week of 19. 10. 2026"
    assert "instr-1 to hana.testova@example.org: Booking API request /api/equipment/instr-1 failed" in alert.text
    assert "instr-2 to ota.zkusebni@example.org: Booking API request /api/equipment/instr-2 failed" in alert.text
    assert "instr-1 to hana.testova@example.org" in visible_text(alert.html)
    assert result.sent == 0
    assert [f.subscription.instrument_id for f in result.failed] == [INSTRUMENT.id, OTHER.id]


def test_one_instrument_failing_does_not_stop_the_others(tmp_path: Path) -> None:
    database = tmp_path / "standup.sqlite3"
    _subscribe_both(database)
    time = FakeTime(MONDAY_8AM)
    source = FailingBookingSource(FakeBookingSource(instruments=[INSTRUMENT, OTHER]), time, failing={INSTRUMENT.id})
    mail_sender = InMemoryMailSender()

    result = _run(source, database, mail_sender, time)

    brief, alert = mail_sender.sent
    assert (brief.to, brief.subject) == ("ota.zkusebni@example.org", "Brief: TEST-LAB - week 19.-25. 10. 2026")
    assert "at 08:00." in brief.text
    assert alert.to == "admin@example.org"
    assert alert.subject == "Nano Stand-up: 1 brief not sent for the week of 19. 10. 2026"
    assert "instr-1 to hana.testova@example.org" in alert.text
    assert "instr-2" not in alert.text
    assert result.sent == 1


def test_a_re_run_after_10_tries_once_then_alerts_without_waiting(tmp_path: Path) -> None:
    database = tmp_path / "standup.sqlite3"
    _subscribe_both(database)
    start = MONDAY_8AM + timedelta(hours=3)
    time = FakeTime(start)
    source = FailingBookingSource(FakeBookingSource(instruments=[INSTRUMENT, OTHER]), time, failing={INSTRUMENT.id})
    mail_sender = InMemoryMailSender()

    _run(source, database, mail_sender, time)

    assert source.failed_at == [start]
    assert [m.to for m in mail_sender.sent] == ["ota.zkusebni@example.org", "admin@example.org"]


def test_a_re_run_after_a_failed_run_sends_only_the_missing_brief(tmp_path: Path) -> None:
    database = tmp_path / "standup.sqlite3"
    _subscribe_both(database)
    time = FakeTime(MONDAY_8AM)
    failing = FailingBookingSource(FakeBookingSource(instruments=[INSTRUMENT, OTHER]), time, failing={INSTRUMENT.id})
    _run(failing, database, InMemoryMailSender(), time)
    re_run = InMemoryMailSender()

    _run(
        FakeBookingSource(instruments=[INSTRUMENT, OTHER]), database, re_run, FakeTime(MONDAY_8AM + timedelta(hours=4))
    )

    assert [m.to for m in re_run.sent] == ["hana.testova@example.org"]


def test_the_run_logs_instruments_briefs_and_errors(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    database = tmp_path / "standup.sqlite3"
    _subscribe_both(database)
    time = FakeTime(MONDAY_8AM + timedelta(hours=3))
    source = FailingBookingSource(FakeBookingSource(instruments=[INSTRUMENT, OTHER]), time, failing={INSTRUMENT.id})
    caplog.set_level(logging.INFO, logger="nano_standup")

    _run(source, database, InMemoryMailSender(), time)

    messages = [r.getMessage() for r in caplog.records]
    assert "Sent brief for TEST-LAB (instr-2) to ota.zkusebni@example.org" in messages
    assert any(
        r.levelno == logging.ERROR and "instr-1 to hana.testova@example.org" in r.getMessage() for r in caplog.records
    )
    assert (
        "Monday job for the week of 19. 10. 2026: 2 instruments processed, 1 brief sent,"
        " 0 already sent before, 1 failed" in messages
    )


def test_a_mail_server_down_all_morning_logs_the_error_report_it_could_not_send(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    database = tmp_path / "standup.sqlite3"
    _subscribe_both(database)
    time = FakeTime(MONDAY_8AM)
    mail_sender = FailingMailSender(time, until=MONDAY_8AM + timedelta(days=1))

    result = _run(FakeBookingSource(instruments=[INSTRUMENT, OTHER]), database, mail_sender, time)

    assert mail_sender.sent == []
    assert len(result.failed) == 2
    assert "Connection refused" in result.failed[0].error
    assert any(
        r.levelno == logging.ERROR and r.getMessage().startswith("Cannot send the error report to the service admin")
        for r in caplog.records
    )
