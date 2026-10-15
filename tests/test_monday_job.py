import logging
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from nano_standup.booking_api import BookingAccessDenied, BookingApiError, BookingDataError
from nano_standup.booking_source import BookingSource, FakeBookingSource
from nano_standup.domain import Certificate, Guarantor, Instrument, Reservation, ReservationStatus, UserFacts, Week
from nano_standup.mail import InMemoryMailSender, MailSender, Message, RecipientRefused
from nano_standup.monday_job import MondayJobResult, run_monday_job
from nano_standup.sent_briefs import SentBriefStore
from nano_standup.subscriptions import Subscription, SubscriptionStore

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

    def get_user_facts(self, instrument_id: str, user_id: str) -> UserFacts:
        self._check(instrument_id)
        return self._source.get_user_facts(instrument_id, user_id)


class FailingMailSender(InMemoryMailSender):
    """Keeps the messages in memory, but the mail server is down until a given time.

    `only_to` set means only mail to that address fails.
    """

    def __init__(self, time: FakeTime, until: datetime, only_to: str | None = None) -> None:
        super().__init__()
        self._time = time
        self._until = until
        self._only_to = only_to
        # The times a message failed, for checking the retries.
        self.failed_at: list[datetime] = []

    def send(self, message: Message) -> None:
        if self._time.now < self._until and self._only_to in (None, message.to):
            self.failed_at.append(self._time.now)
            raise ConnectionRefusedError(111, "Connection refused")
        super().send(message)


class FailingSubscriptionStore(SubscriptionStore):
    """The real subscriptions, but ending one fails with a database error."""

    def end_because_guarantor_changed(self, subscription: Subscription, now: datetime) -> bool:
        raise sqlite3.OperationalError("database is locked")


class RefusingMailSender(InMemoryMailSender):
    """Keeps the messages in memory, but the mail server refuses one recipient address."""

    def __init__(self, refused: str) -> None:
        super().__init__()
        self._refused = refused

    def send(self, message: Message) -> None:
        if message.to == self._refused:
            raise RecipientRefused(f"The mail server refused {message.to}: 550 5.1.1 No such user")
        super().send(message)


def _refuse_records(database: Path, instrument_id: str) -> None:
    """Make the real database refuse to record a sent brief for the instrument, as a full disk would."""
    SentBriefStore(database)
    with closing(sqlite3.connect(database)) as db, db:
        db.execute(
            "CREATE TRIGGER refuse_record BEFORE INSERT ON sent_brief"
            f" WHEN NEW.instrument_id = '{instrument_id}'"
            " BEGIN SELECT RAISE(ABORT, 'database or disk is full'); END"
        )


def _run(
    source: BookingSource,
    database: Path,
    mail_sender: MailSender,
    time: FakeTime | None = None,
    sent_briefs: SentBriefStore | None = None,
    subscriptions: SubscriptionStore | None = None,
) -> MondayJobResult:
    """Runs the Monday job with the default heads-up settings. The time is Monday 08:00 unless given."""
    time = time or FakeTime(MONDAY_8AM)
    return run_monday_job(
        source,
        subscriptions or SubscriptionStore(database),
        sent_briefs or SentBriefStore(database),
        mail_sender,
        clock=time.clock,
        sleep=time.sleep,
        from_address="standup@example.org",
        service_admin_email="service-admin@example.org",
        heads_up_settings=HEADS_UP_SETTINGS,
    )


def _earlier_reservations(user_name: str) -> list[Reservation]:
    """3 reservations in January, after OLD_CERTIFICATE starts, so the user is not a new user."""
    return [reservation(f"2026-01-0{day}T09:00", f"2026-01-0{day}T10:00", user_name=user_name) for day in (5, 6, 7)]


def _brief_for(tmp_path: Path, reservations: list[Reservation], certificates: dict[str, list[Certificate]]) -> Message:
    """The one brief the Monday job sends for the test instrument. `certificates` by user id."""
    source = FakeBookingSource([INSTRUMENT], {INSTRUMENT.id: reservations}, {INSTRUMENT.id: certificates})
    database = tmp_path / "standup.sqlite3"
    SubscriptionStore(database).add_active(INSTRUMENT.id, GUARANTOR.email, MONDAY_8AM)
    mail_sender = InMemoryMailSender()
    _run(source, database, mail_sender)
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
    assert error_report.to == "service-admin@example.org"
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


def test_a_re_run_on_tuesday_retries_until_10_on_tuesday(tmp_path: Path) -> None:
    database = tmp_path / "standup.sqlite3"
    SubscriptionStore(database).add_active(INSTRUMENT.id, "hana.testova@example.org", MONDAY_8AM)
    tuesday_8am = MONDAY_8AM + timedelta(days=1)
    time = FakeTime(tuesday_8am)
    source = FailingBookingSource(FakeBookingSource(instruments=[INSTRUMENT]), time, failing={INSTRUMENT.id})

    _run(source, database, InMemoryMailSender(), time)

    assert source.failed_at == [tuesday_8am + timedelta(minutes=15 * i) for i in range(9)]


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
    assert error_report.to == "service-admin@example.org"
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
    assert [m.to for m in mail_sender.sent] == ["ota.zkusebni@example.org", "service-admin@example.org"]


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
        " 0 briefs already sent before, 1 brief failed, 0 briefs sent but not recorded,"
        " 0 subscriptions ended because the guarantor changed" in messages
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
    mail_sender = RefusingMailSender("service-admin@example.org")

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

    _refuse_records(database, INSTRUMENT.id)

    result = _run(FakeBookingSource(instruments=[INSTRUMENT, OTHER]), database, mail_sender, time)

    first, second, error_report = mail_sender.sent
    assert [first.to, second.to] == ["hana.testova@example.org", "ota.zkusebni@example.org"]
    assert error_report.to == "service-admin@example.org"
    assert error_report.subject == "Nano Stand-up: 1 brief sent but not recorded for the week of 19. 10. 2026"
    assert "TEST-SEM (instr-1) to hana.testova@example.org: database or disk is full" in error_report.text
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

    _refuse_records(database, OTHER.id)

    _run(source, database, mail_sender, time)

    brief, error_report = mail_sender.sent
    assert error_report.subject == (
        "Nano Stand-up: 1 brief not sent, 1 brief sent but not recorded for the week of 19. 10. 2026"
    )
    not_sent, not_recorded = error_report.text.split("sent but not recorded", 1)
    assert "instr-1 to hana.testova@example.org" in not_sent
    assert "TEST-LAB (instr-2) to ota.zkusebni@example.org: database or disk is full" in not_recorded


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

    def get_user_facts(self, instrument_id: str, user_id: str) -> UserFacts:
        return self._source.get_user_facts(instrument_id, user_id)


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("Unexpected bug"),
        BookingDataError("A reservation has no 'id' in the booking API answer"),
        BookingAccessDenied("No access to instrument 'instr-1' in the booking system"),
    ],
    ids=["unexpected", "bad data", "no access"],
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


def _with_guarantor(instrument: Instrument, name: str, email: str) -> Instrument:
    return replace(instrument, guarantor=Guarantor(name=name, email=email, user_id=f"user-{name}"))


def test_a_guarantor_email_in_another_case_and_with_spaces_still_gets_the_brief(tmp_path: Path) -> None:
    database = tmp_path / "standup.sqlite3"
    SubscriptionStore(database).add_active(INSTRUMENT.id, "hana.testova@example.org", MONDAY_8AM)
    instrument = _with_guarantor(INSTRUMENT, "Hana Testova", "  Hana.Testova@Example.ORG ")
    mail_sender = InMemoryMailSender()

    result = _run(FakeBookingSource(instruments=[instrument]), database, mail_sender)

    [brief] = mail_sender.sent
    assert (brief.to, brief.subject) == ("hana.testova@example.org", "Brief: TEST-SEM - week 19.-25. 10. 2026")
    assert SubscriptionStore(database).list_active() == [Subscription(INSTRUMENT.id, "hana.testova@example.org")]
    assert (result.sent, result.subscriptions_ended) == (1, 0)


@pytest.mark.parametrize(
    ("name", "email"),
    [("Ota Zkusebni", "ota.zkusebni@example.org"), ("Hana Testova", "hana.novotna@example.org")],
    ids=["guarantor changed", "guarantor email changed"],
)
def test_a_subscriber_no_longer_the_guarantor_gets_a_stop_notice_and_no_brief(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, name: str, email: str
) -> None:
    database = tmp_path / "standup.sqlite3"
    _subscribe_both(database)
    source = FakeBookingSource(
        instruments=[_with_guarantor(INSTRUMENT, name, email), OTHER],
        reservations={INSTRUMENT.id: [reservation("2026-10-20T09:00", "2026-10-20T11:00", user_name="Jana Smyslena")]},
    )
    mail_sender = InMemoryMailSender()
    caplog.set_level(logging.INFO, logger="nano_standup")

    result = _run(source, database, mail_sender)

    notice, brief = mail_sender.sent
    assert (notice.to, notice.from_address) == ("hana.testova@example.org", "standup@example.org")
    assert notice.subject == "Your brief for TEST-SEM has stopped"
    for version in (notice.text, visible_text(notice.html)):
        assert "no longer lists this email address as the guarantor of TEST-SEM" in version
        assert "subscribe again" in version
        # The stop notice never shows the reservations or who the guarantor is now.
        assert "Jana Smyslena" not in version
        assert name not in version
        assert email not in version
    assert brief.to == "ota.zkusebni@example.org"
    assert SubscriptionStore(database).list_active() == [Subscription(OTHER.id, "ota.zkusebni@example.org")]
    assert (result.sent, result.subscriptions_ended, result.failed) == (1, 1, ())
    assert any(
        r.levelno == logging.WARNING
        and r.getMessage().startswith("Subscription for TEST-SEM (instr-1) to hana.testova@example.org ended")
        for r in caplog.records
    )


class CountingBookingSource(FakeBookingSource):
    """The fake booking source, but it keeps the instruments whose reservations or user facts the job read."""

    def __init__(
        self,
        instruments: list[Instrument],
        reservations: dict[str, list[Reservation]],
        certificates: dict[str, dict[str, list[Certificate]]],
    ) -> None:
        super().__init__(instruments, reservations, certificates)
        self.read: list[str] = []

    def get_reservations(self, instrument_id: str, week: Week) -> list[Reservation]:
        self.read.append(instrument_id)
        return super().get_reservations(instrument_id, week)

    def get_user_facts(self, instrument_id: str, user_id: str) -> UserFacts:
        self.read.append(instrument_id)
        return super().get_user_facts(instrument_id, user_id)


def test_a_subscription_that_ends_reads_no_reservations_or_user_facts(tmp_path: Path) -> None:
    database = tmp_path / "standup.sqlite3"
    _subscribe_both(database)
    jana = reservation("2026-10-20T09:00", "2026-10-20T11:00", user_name="Jana Smyslena")
    source = CountingBookingSource(
        [_with_guarantor(INSTRUMENT, "Ota Zkusebni", "ota.zkusebni@example.org"), OTHER],
        reservations={INSTRUMENT.id: [jana], OTHER.id: [jana]},
        certificates={INSTRUMENT.id: {JANA: [OLD_CERTIFICATE]}, OTHER.id: {JANA: [OLD_CERTIFICATE]}},
    )

    result = _run(source, database, InMemoryMailSender())

    # The guarantor check comes first, so the ended subscription costs no reads for a brief.
    assert source.read == [OTHER.id, OTHER.id]
    assert (result.sent, result.subscriptions_ended) == (1, 1)


def test_a_re_run_in_the_same_week_sends_no_second_stop_notice(tmp_path: Path) -> None:
    database = tmp_path / "standup.sqlite3"
    _subscribe_both(database)
    source = FakeBookingSource(
        instruments=[_with_guarantor(INSTRUMENT, "Ota Zkusebni", "ota.zkusebni@example.org"), OTHER]
    )
    first_run = InMemoryMailSender()
    _run(source, database, first_run)
    re_run = InMemoryMailSender()

    result = _run(source, database, re_run, FakeTime(MONDAY_8AM + timedelta(hours=3)))

    assert [m.subject for m in first_run.sent if m.to == "hana.testova@example.org"] == [
        "Your brief for TEST-SEM has stopped"
    ]
    assert [m.to for m in re_run.sent] == []
    assert (result.subscriptions_ended, result.already_sent) == (0, 1)


def test_the_next_week_sends_nothing_to_a_subscription_that_ended(tmp_path: Path) -> None:
    database = tmp_path / "standup.sqlite3"
    _subscribe_both(database)
    source = FakeBookingSource(
        instruments=[_with_guarantor(INSTRUMENT, "Ota Zkusebni", "ota.zkusebni@example.org"), OTHER]
    )
    _run(source, database, InMemoryMailSender())
    next_week = InMemoryMailSender()

    result = _run(source, database, next_week, FakeTime(MONDAY_8AM + timedelta(days=7)))

    assert [(m.to, m.subject) for m in next_week.sent] == [
        ("ota.zkusebni@example.org", "Brief: TEST-LAB - week 26. 10.-1. 11. 2026")
    ]
    assert (result.sent, result.subscriptions_ended) == (1, 0)


def test_a_stop_notice_that_fails_is_tried_again_with_the_briefs(tmp_path: Path) -> None:
    database = tmp_path / "standup.sqlite3"
    _subscribe_both(database)
    time = FakeTime(MONDAY_8AM)
    # The mail server is back at 08:20, so the third try at 08:30 works.
    mail_sender = FailingMailSender(time, until=MONDAY_8AM + timedelta(minutes=20))
    source = FakeBookingSource(
        instruments=[_with_guarantor(INSTRUMENT, "Ota Zkusebni", "ota.zkusebni@example.org"), OTHER]
    )

    result = _run(source, database, mail_sender, time)

    assert sorted((m.to, m.subject) for m in mail_sender.sent) == [
        ("hana.testova@example.org", "Your brief for TEST-SEM has stopped"),
        ("ota.zkusebni@example.org", "Brief: TEST-LAB - week 19.-25. 10. 2026"),
    ]
    assert time.now == MONDAY_8AM + timedelta(minutes=30)
    assert (result.sent, result.subscriptions_ended, result.failed, result.stop_notices_not_sent) == (1, 1, (), ())


def test_a_stop_notice_that_cannot_be_sent_still_ends_the_subscription_and_is_in_the_error_report(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    database = tmp_path / "standup.sqlite3"
    _subscribe_both(database)
    time = FakeTime(MONDAY_8AM)
    # Mail to the former subscriber fails all morning.
    mail_sender = FailingMailSender(time, until=MONDAY_8AM + timedelta(days=1), only_to="hana.testova@example.org")
    source = FakeBookingSource(
        instruments=[_with_guarantor(INSTRUMENT, "Ota Zkusebni", "ota.zkusebni@example.org"), OTHER]
    )

    result = _run(source, database, mail_sender, time)

    # Tried every 15 minutes up to 10:00, then given up; no brief goes to the former subscriber.
    assert mail_sender.failed_at == [MONDAY_8AM + timedelta(minutes=15 * i) for i in range(9)]
    brief, error_report = mail_sender.sent
    assert brief.to == "ota.zkusebni@example.org"
    assert error_report.to == "service-admin@example.org"
    assert error_report.subject == "Nano Stand-up: 1 stop notice not sent for the week of 19. 10. 2026"
    assert "TEST-SEM (instr-1) to hana.testova@example.org: [Errno 111] Connection refused" in error_report.text
    assert "TEST-SEM (instr-1) to hana.testova@example.org" in visible_text(error_report.html)
    assert SubscriptionStore(database).list_active() == [Subscription(OTHER.id, "ota.zkusebni@example.org")]
    assert (result.sent, result.subscriptions_ended, result.failed) == (1, 1, ())
    assert [f.subscription.instrument_id for f in result.stop_notices_not_sent] == [INSTRUMENT.id]
    assert any(
        r.levelno == logging.ERROR and "stop notice" in r.getMessage() and "hana.testova@example.org" in r.getMessage()
        for r in caplog.records
    )


def test_a_stop_notice_after_a_late_start_is_tried_again_on_the_quarter_hours(tmp_path: Path) -> None:
    database = tmp_path / "standup.sqlite3"
    _subscribe_both(database)
    started = MONDAY_8AM + timedelta(seconds=40)
    time = FakeTime(started)
    mail_sender = FailingMailSender(time, until=MONDAY_8AM + timedelta(days=1), only_to="hana.testova@example.org")
    source = FakeBookingSource(
        instruments=[_with_guarantor(INSTRUMENT, "Ota Zkusebni", "ota.zkusebni@example.org"), OTHER]
    )

    result = _run(source, database, mail_sender, time)

    assert mail_sender.failed_at == [started] + [MONDAY_8AM + timedelta(minutes=15 * i) for i in range(1, 9)]
    assert [f.subscription.instrument_id for f in result.stop_notices_not_sent] == [INSTRUMENT.id]


def test_a_stop_notice_to_a_refused_address_fails_at_once_and_goes_to_the_error_report(tmp_path: Path) -> None:
    database = tmp_path / "standup.sqlite3"
    _subscribe_both(database)
    source = FakeBookingSource(
        instruments=[_with_guarantor(INSTRUMENT, "Ota Zkusebni", "ota.zkusebni@example.org"), OTHER]
    )
    time = FakeTime(MONDAY_8AM)
    mail_sender = RefusingMailSender("hana.testova@example.org")

    result = _run(source, database, mail_sender, time)

    brief, error_report = mail_sender.sent
    assert brief.to == "ota.zkusebni@example.org"
    assert error_report.subject == "Nano Stand-up: 1 stop notice not sent for the week of 19. 10. 2026"
    # Reported as the refusal it is, not as an unexpected error.
    assert (
        "TEST-SEM (instr-1) to hana.testova@example.org:"
        " The mail server refused hana.testova@example.org: 550 5.1.1 No such user"
    ) in error_report.text
    assert SubscriptionStore(database).list_active() == [Subscription(OTHER.id, "ota.zkusebni@example.org")]
    assert (result.sent, result.subscriptions_ended, result.failed) == (1, 1, ())
    # A retry cannot change the mail server's answer, so the stop notice is not tried again.
    assert time.now == MONDAY_8AM


class EndedByAnotherRunMeanwhile(FakeBookingSource):
    """The fake booking source, but another run ends one subscription while this run reads its instrument."""

    def __init__(self, instruments: list[Instrument], store: SubscriptionStore, ended: Subscription) -> None:
        super().__init__(instruments)
        self._store = store
        self._ended = ended

    def get_instrument(self, instrument_id: str) -> Instrument:
        if instrument_id == self._ended.instrument_id:
            self._store.end_because_guarantor_changed(self._ended, MONDAY_8AM)
        return super().get_instrument(instrument_id)


def test_a_subscription_another_run_ended_meanwhile_gets_no_second_stop_notice(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    database = tmp_path / "standup.sqlite3"
    _subscribe_both(database)
    source = EndedByAnotherRunMeanwhile(
        [_with_guarantor(INSTRUMENT, "Ota Zkusebni", "ota.zkusebni@example.org"), OTHER],
        SubscriptionStore(database),
        Subscription(INSTRUMENT.id, "hana.testova@example.org"),
    )
    mail_sender = InMemoryMailSender()
    caplog.set_level(logging.INFO, logger="nano_standup")

    result = _run(source, database, mail_sender)

    assert [m.to for m in mail_sender.sent] == ["ota.zkusebni@example.org"]
    # This run did not end it, so it does not count it as ended.
    assert (result.sent, result.subscriptions_ended, result.stop_notices_not_sent) == (1, 0, ())
    assert any(
        "1 brief sent," in r.getMessage() and "0 subscriptions ended because the guarantor changed" in r.getMessage()
        for r in caplog.records
    )


class BrokenMailSender(InMemoryMailSender):
    """Keeps the messages in memory, but sending to one address raises an unexpected error."""

    def __init__(self, broken_to: str) -> None:
        super().__init__()
        self._broken_to = broken_to

    def send(self, message: Message) -> None:
        if message.to == self._broken_to:
            raise RuntimeError("Unexpected bug in the mail sender")
        super().send(message)


def test_an_unexpected_error_sending_a_stop_notice_does_not_stop_the_run(tmp_path: Path) -> None:
    database = tmp_path / "standup.sqlite3"
    _subscribe_both(database)
    source = FakeBookingSource(
        instruments=[_with_guarantor(INSTRUMENT, "Ota Zkusebni", "ota.zkusebni@example.org"), OTHER]
    )
    time = FakeTime(MONDAY_8AM)
    mail_sender = BrokenMailSender(broken_to="hana.testova@example.org")

    result = _run(source, database, mail_sender, time)

    brief, error_report = mail_sender.sent
    assert brief.to == "ota.zkusebni@example.org"
    assert error_report.subject == "Nano Stand-up: 1 stop notice not sent for the week of 19. 10. 2026"
    assert (
        "TEST-SEM (instr-1) to hana.testova@example.org: RuntimeError: Unexpected bug in the mail sender"
        in error_report.text
    )
    assert SubscriptionStore(database).list_active() == [Subscription(OTHER.id, "ota.zkusebni@example.org")]
    assert (result.sent, result.subscriptions_ended, result.failed) == (1, 1, ())
    # A retry cannot fix an unexpected error, so the stop notice is not tried again.
    assert time.now == MONDAY_8AM


def test_a_subscription_that_cannot_be_ended_gets_no_brief_and_is_in_the_error_report(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    database = tmp_path / "standup.sqlite3"
    _subscribe_both(database)
    time = FakeTime(MONDAY_8AM)
    source = FakeBookingSource(
        instruments=[_with_guarantor(INSTRUMENT, "Ota Zkusebni", "ota.zkusebni@example.org"), OTHER]
    )
    mail_sender = InMemoryMailSender()

    result = _run(source, database, mail_sender, time, subscriptions=FailingSubscriptionStore(database))

    brief, error_report = mail_sender.sent
    assert brief.to == "ota.zkusebni@example.org"
    assert error_report.subject == "Nano Stand-up: 1 brief not sent for the week of 19. 10. 2026"
    assert (
        "TEST-SEM (instr-1) to hana.testova@example.org: Could not end the subscription: database is locked"
        in error_report.text
    )
    assert "unexpected" not in error_report.text
    # A locked database may be free later, so it is tried again up to 10:00.
    assert time.now == MONDAY_8AM + timedelta(hours=2)
    assert (result.sent, result.subscriptions_ended, len(result.failed)) == (1, 0, 1)
    assert any(
        r.levelno == logging.ERROR
        and r.getMessage().startswith(
            "Could not end the subscription for TEST-SEM (instr-1) to hana.testova@example.org, so no brief was sent"
        )
        for r in caplog.records
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
