import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nano_standup.booking_api import BookingApiError, BookingDataError
from nano_standup.booking_source import BookingSource, FakeBookingSource
from nano_standup.domain import Guarantor, Instrument, Reservation, ReservationStatus, Week
from nano_standup.mail import InMemoryMailSender, MailSender, Message, RecipientRefused
from nano_standup.monday_job import MondayJobResult, run_monday_job
from nano_standup.sent_briefs import SentBriefStore
from nano_standup.subscriptions import Subscription, SubscriptionStore

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
        self,
        source: FakeBookingSource,
        time: FakeTime,
        failing: set[str],
        until: datetime | None = None,
        takes: timedelta = timedelta(),
    ) -> None:
        """`until` None means the failure never ends. Each failing request moves the clock on by `takes`."""
        self._source = source
        self._time = time
        self._failing = failing
        self._until = until
        self._takes = takes
        # The times the job asked for a failing instrument, for checking the retries.
        self.failed_at: list[datetime] = []

    def _check(self, instrument_id: str) -> None:
        if instrument_id in self._failing and (self._until is None or self._time.now < self._until):
            self.failed_at.append(self._time.now)
            self._time.now += self._takes
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


class RefusingMailSender(InMemoryMailSender):
    """Keeps the messages in memory, but the mail server refuses one recipient address."""

    def __init__(self, refused: str) -> None:
        super().__init__()
        self._refused = refused

    def send(self, message: Message) -> None:
        if message.to == self._refused:
            raise RecipientRefused(f"The mail server refused {message.to}: 550 5.1.1 No such user")
        super().send(message)


class FailingSentBriefStore(SentBriefStore):
    """The real record of sent briefs, but recording fails for some instruments."""

    def __init__(self, path: Path, failing: set[str]) -> None:
        super().__init__(path)
        self._failing = failing

    def record(self, subscription: Subscription, week: Week, sent_at: datetime) -> None:
        if subscription.instrument_id in self._failing:
            raise sqlite3.OperationalError("database is locked")
        super().record(subscription, week, sent_at)


def _run(
    source: BookingSource,
    database: Path,
    mail_sender: MailSender,
    time: FakeTime | None = None,
    sent_briefs: SentBriefStore | None = None,
) -> MondayJobResult:
    """Runs the Monday job with the default working hours. The time is Monday 08:00 unless given."""
    time = time or FakeTime(MONDAY_8AM)
    return run_monday_job(
        source,
        SubscriptionStore(database),
        sent_briefs or SentBriefStore(database),
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


def test_a_failure_still_there_at_10_sends_one_error_report_to_the_service_admin(tmp_path: Path) -> None:
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
    [error_report] = mail_sender.sent
    assert error_report.to == "admin@example.org"
    assert error_report.subject == "Nano Stand-up: 2 briefs not sent for the week of 19. 10. 2026"
    assert "instr-1 to hana.testova@example.org: Booking API request /api/equipment/instr-1 failed" in error_report.text
    assert "instr-2 to ota.zkusebni@example.org: Booking API request /api/equipment/instr-2 failed" in error_report.text
    assert "instr-1 to hana.testova@example.org" in visible_text(error_report.html)
    assert result.sent == 0
    assert [f.subscription.instrument_id for f in result.failed] == [INSTRUMENT.id, OTHER.id]


def test_a_job_started_a_few_seconds_late_still_tries_at_10(tmp_path: Path) -> None:
    database = tmp_path / "standup.sqlite3"
    SubscriptionStore(database).add_active(INSTRUMENT.id, "hana.testova@example.org", MONDAY_8AM)
    start = MONDAY_8AM + timedelta(seconds=5)
    time = FakeTime(start)
    source = FailingBookingSource(FakeBookingSource(instruments=[INSTRUMENT]), time, failing={INSTRUMENT.id})

    _run(source, database, InMemoryMailSender(), time)

    # The retries keep to the 15-minute marks of the Prague clock, so the last one is at 10:00.
    assert source.failed_at == [start] + [MONDAY_8AM + timedelta(minutes=15 * i) for i in range(1, 9)]


def test_a_pass_longer_than_the_retry_interval_waits_for_the_next_mark_still_ahead(tmp_path: Path) -> None:
    database = tmp_path / "standup.sqlite3"
    SubscriptionStore(database).add_active(INSTRUMENT.id, "hana.testova@example.org", MONDAY_8AM)
    time = FakeTime(MONDAY_8AM)
    # Each try takes 20 minutes, so the 08:15 mark has passed when the first try ends.
    source = FailingBookingSource(
        FakeBookingSource(instruments=[INSTRUMENT]), time, failing={INSTRUMENT.id}, takes=timedelta(minutes=20)
    )

    _run(source, database, InMemoryMailSender(), time)

    assert source.failed_at == [MONDAY_8AM + timedelta(minutes=30 * i) for i in range(5)]


def test_one_instrument_failing_does_not_stop_the_others(tmp_path: Path) -> None:
    database = tmp_path / "standup.sqlite3"
    _subscribe_both(database)
    time = FakeTime(MONDAY_8AM)
    source = FailingBookingSource(FakeBookingSource(instruments=[INSTRUMENT, OTHER]), time, failing={INSTRUMENT.id})
    mail_sender = InMemoryMailSender()

    result = _run(source, database, mail_sender, time)

    brief, error_report = mail_sender.sent
    assert (brief.to, brief.subject) == ("ota.zkusebni@example.org", "Brief: TEST-LAB - week 19.-25. 10. 2026")
    assert "at 08:00." in brief.text
    assert error_report.to == "admin@example.org"
    assert error_report.subject == "Nano Stand-up: 1 brief not sent for the week of 19. 10. 2026"
    assert "instr-1 to hana.testova@example.org" in error_report.text
    assert "instr-2" not in error_report.text
    assert result.sent == 1


def test_a_re_run_after_10_tries_once_then_sends_the_error_report_without_waiting(tmp_path: Path) -> None:
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
        " 0 briefs already sent before, 1 brief failed, 0 briefs sent but not recorded" in messages
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


def test_a_refused_service_admin_address_logs_the_error_report_it_could_not_send(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    database = tmp_path / "standup.sqlite3"
    SubscriptionStore(database).add_active("instr-gone", "hana.testova@example.org", MONDAY_8AM)
    mail_sender = RefusingMailSender("admin@example.org")

    result = _run(FakeBookingSource(instruments=[]), database, mail_sender)

    assert mail_sender.sent == []
    assert len(result.failed) == 1
    assert any(
        r.levelno == logging.ERROR and r.getMessage().startswith("Cannot send the error report to the service admin")
        for r in caplog.records
    )


def test_a_brief_sent_but_not_recorded_does_not_stop_the_others_and_is_in_the_error_report(
    tmp_path: Path,
) -> None:
    database = tmp_path / "standup.sqlite3"
    _subscribe_both(database)
    time = FakeTime(MONDAY_8AM)
    mail_sender = InMemoryMailSender()

    result = _run(
        FakeBookingSource(instruments=[INSTRUMENT, OTHER]),
        database,
        mail_sender,
        time,
        sent_briefs=FailingSentBriefStore(database, failing={INSTRUMENT.id}),
    )

    first, second, error_report = mail_sender.sent
    assert [first.to, second.to] == ["hana.testova@example.org", "ota.zkusebni@example.org"]
    assert error_report.to == "admin@example.org"
    assert error_report.subject == "Nano Stand-up: 1 brief sent but not recorded for the week of 19. 10. 2026"
    assert "TEST-SEM (instr-1) to hana.testova@example.org: database is locked" in error_report.text
    assert "a re-run sends them again" in error_report.text
    # Sent, so not tried again.
    assert time.now == MONDAY_8AM
    assert (result.sent, result.failed) == (2, ())
    assert [f.subscription.instrument_id for f in result.not_recorded] == [INSTRUMENT.id]


def test_a_refused_recipient_fails_at_once_and_goes_to_the_error_report(tmp_path: Path) -> None:
    database = tmp_path / "standup.sqlite3"
    _subscribe_both(database)
    time = FakeTime(MONDAY_8AM)
    mail_sender = RefusingMailSender("hana.testova@example.org")

    result = _run(FakeBookingSource(instruments=[INSTRUMENT, OTHER]), database, mail_sender, time)

    brief, error_report = mail_sender.sent
    assert brief.to == "ota.zkusebni@example.org"
    assert "TEST-SEM (instr-1) to hana.testova@example.org: " in error_report.text
    assert "No such user" in error_report.text
    assert time.now == MONDAY_8AM
    assert [f.subscription.instrument_id for f in result.failed] == [INSTRUMENT.id]


def test_an_instrument_not_in_the_booking_system_fails_at_once_without_retries(tmp_path: Path) -> None:
    database = tmp_path / "standup.sqlite3"
    store = SubscriptionStore(database)
    store.add_active("instr-gone", "hana.testova@example.org", MONDAY_8AM)
    store.add_active(OTHER.id, "ota.zkusebni@example.org", MONDAY_8AM)
    time = FakeTime(MONDAY_8AM)
    mail_sender = InMemoryMailSender()

    result = _run(FakeBookingSource(instruments=[OTHER]), database, mail_sender, time)

    brief, error_report = mail_sender.sent
    assert brief.to == "ota.zkusebni@example.org"
    assert error_report.subject == "Nano Stand-up: 1 brief not sent for the week of 19. 10. 2026"
    assert "instr-gone to hana.testova@example.org: No instrument with id 'instr-gone'" in error_report.text
    assert time.now == MONDAY_8AM
    assert [f.subscription.instrument_id for f in result.failed] == ["instr-gone"]


def test_the_error_report_lists_briefs_not_sent_and_briefs_not_recorded(tmp_path: Path) -> None:
    database = tmp_path / "standup.sqlite3"
    _subscribe_both(database)
    time = FakeTime(MONDAY_8AM + timedelta(hours=3))
    source = FailingBookingSource(FakeBookingSource(instruments=[INSTRUMENT, OTHER]), time, failing={INSTRUMENT.id})
    mail_sender = InMemoryMailSender()

    _run(source, database, mail_sender, time, sent_briefs=FailingSentBriefStore(database, failing={OTHER.id}))

    brief, error_report = mail_sender.sent
    assert error_report.subject == (
        "Nano Stand-up: 1 brief not sent, 1 brief sent but not recorded for the week of 19. 10. 2026"
    )
    not_sent, not_recorded = error_report.text.split("sent but not recorded", 1)
    assert "instr-1 to hana.testova@example.org" in not_sent
    assert "TEST-LAB (instr-2) to ota.zkusebni@example.org: database is locked" in not_recorded


class BrokenBookingSource:
    """The fake booking source, but reading one instrument's reservations raises the given error."""

    def __init__(self, source: FakeBookingSource, broken: str, error: Exception) -> None:
        self._source = source
        self._broken = broken
        self._error = error

    def get_instrument(self, instrument_id: str) -> Instrument:
        return self._source.get_instrument(instrument_id)

    def get_reservations(self, instrument_id: str, week: Week) -> list[Reservation]:
        if instrument_id == self._broken:
            raise self._error
        return self._source.get_reservations(instrument_id, week)


@pytest.mark.parametrize(
    "error",
    [RuntimeError("Unexpected bug"), BookingDataError("A reservation has no 'id' in the booking API answer")],
    ids=["unexpected", "bad data"],
)
def test_an_error_a_retry_cannot_fix_goes_straight_to_the_error_report(tmp_path: Path, error: Exception) -> None:
    database = tmp_path / "standup.sqlite3"
    _subscribe_both(database)
    time = FakeTime(MONDAY_8AM)
    source = BrokenBookingSource(FakeBookingSource(instruments=[INSTRUMENT, OTHER]), INSTRUMENT.id, error)
    mail_sender = InMemoryMailSender()

    result = _run(source, database, mail_sender, time)

    brief, error_report = mail_sender.sent
    assert brief.to == "ota.zkusebni@example.org"
    # The instrument's alias is known here, so the error report names it.
    assert "TEST-SEM (instr-1) to hana.testova@example.org: " in error_report.text
    assert str(error) in error_report.text
    assert time.now == MONDAY_8AM
    assert (result.sent, len(result.failed)) == (1, 1)


class OtherRunMeanwhile(FakeBookingSource):
    """The fake booking source, but another run sends one brief while this run reads that instrument."""

    def __init__(self, instruments: list[Instrument], sent_briefs: SentBriefStore, sent_by_the_other: str) -> None:
        super().__init__(instruments)
        self._sent_briefs = sent_briefs
        self._sent_by_the_other = sent_by_the_other

    def get_reservations(self, instrument_id: str, week: Week) -> list[Reservation]:
        if instrument_id == self._sent_by_the_other:
            subscription = Subscription(instrument_id, "hana.testova@example.org")
            self._sent_briefs.record(subscription, week, MONDAY_8AM)
        return super().get_reservations(instrument_id, week)


def test_a_brief_another_run_sent_meanwhile_is_not_sent_again(tmp_path: Path) -> None:
    database = tmp_path / "standup.sqlite3"
    _subscribe_both(database)
    sent_briefs = SentBriefStore(database)
    source = OtherRunMeanwhile([INSTRUMENT, OTHER], sent_briefs, sent_by_the_other=INSTRUMENT.id)
    mail_sender = InMemoryMailSender()

    result = _run(source, database, mail_sender, sent_briefs=sent_briefs)

    assert [m.to for m in mail_sender.sent] == ["ota.zkusebni@example.org"]
    assert (result.sent, result.already_sent) == (1, 1)
