"""Monday job: builds and sends this week's brief for every active subscription.

The server timer starts it once, at 08:00 Prague time.
A brief that fails is tried again every RETRY_INTERVAL until RETRY_DEADLINE,
unless its instrument is no longer in the booking system.
What still fails then goes to the service admin in one error report.
"""

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from html import escape

from nano_standup.booking_api import BookingApiError, BookingDataError
from nano_standup.booking_source import BookingSource, InstrumentNotFound
from nano_standup.brief import build_brief
from nano_standup.domain import PRAGUE, Clock, Week, WorkingHours
from nano_standup.mail import MailSender, Message
from nano_standup.sent_briefs import SentBriefStore
from nano_standup.subscriptions import Subscription, SubscriptionStore

log = logging.getLogger(__name__)

# Waits for the given time. Passed in from outside, so tests can move a fake clock instead of waiting.
Sleep = Callable[[timedelta], None]

RETRY_INTERVAL = timedelta(minutes=15)
# Prague time. The last try is at this time or just before it; then the service admin gets the error report.
RETRY_DEADLINE = time(10, 0)

# The failures one brief can have that a later try may not have: the booking API or the mail server is down,
# or the booking system has an instrument without the data a brief needs. OSError covers all SMTP errors.
RETRYABLE_ERRORS = (BookingApiError, BookingDataError, OSError)
# The failures a later try cannot fix, so the brief fails at once.
PERMANENT_ERRORS = (InstrumentNotFound,)


@dataclass(frozen=True)
class BriefFailure:
    subscription: Subscription
    # The error message of the last try.
    error: str


@dataclass(frozen=True)
class MondayJobResult:
    # Briefs sent by this run, recorded or not.
    sent: int
    # Briefs a run before already sent this week, so this run skipped them.
    already_sent: int
    # Briefs still not sent at the end of the run.
    failed: tuple[BriefFailure, ...]
    # Briefs sent, but the record of sent briefs could not save them, so a re-run sends them again.
    not_recorded: tuple[BriefFailure, ...]


def run_monday_job(
    source: BookingSource,
    subscriptions: SubscriptionStore,
    sent_briefs: SentBriefStore,
    mail_sender: MailSender,
    *,
    clock: Clock,
    sleep: Sleep,
    from_address: str,
    service_admin_email: str,
    working_hours: WorkingHours,
) -> MondayJobResult:
    """Send the briefs not yet sent this week, with retries. The week is the one that holds today in Prague.

    A run started after the deadline, such as a manual re-run, tries each brief once.
    """
    started = clock().astimezone(PRAGUE)
    week = Week.containing(started.date())
    deadline = datetime.combine(started.date(), RETRY_DEADLINE, tzinfo=PRAGUE)
    sender = _BriefSender(source, sent_briefs, mail_sender, clock, from_address, working_hours, week)
    active = subscriptions.list_active()
    to_send = [s for s in active if not sent_briefs.was_sent(s, week)]

    outcome = _send_with_retries(sender, to_send, clock, sleep, deadline)
    sent = len(to_send) - len(outcome.failed)
    already_sent = len(active) - len(to_send)
    if outcome.failed or outcome.not_recorded:
        _send_error_report(mail_sender, from_address, service_admin_email, week, outcome)
    log.info(
        "Monday job for the week of %s: %s processed, %s sent, %s already sent before, %s failed,"
        " %s sent but not recorded",
        _date(week),
        _count(len({s.instrument_id for s in to_send}), "instrument"),
        _count(sent, "brief"),
        _count(already_sent, "brief"),
        _count(len(outcome.failed), "brief"),
        _count(len(outcome.not_recorded), "brief"),
    )
    return MondayJobResult(
        sent=sent,
        already_sent=already_sent,
        failed=tuple(outcome.failed),
        not_recorded=tuple(outcome.not_recorded),
    )


@dataclass
class _Outcome:
    """What went wrong in one or more passes over the briefs."""

    failed: list[BriefFailure] = field(default_factory=list)
    not_recorded: list[BriefFailure] = field(default_factory=list)


def _send_with_retries(
    sender: "_BriefSender", to_send: list[Subscription], clock: Clock, sleep: Sleep, deadline: datetime
) -> _Outcome:
    """Try the briefs, then try the ones that may work later again every RETRY_INTERVAL, up to the deadline."""
    outcome = _Outcome()
    pending = to_send
    while True:
        pass_started = clock()
        to_retry = sender.try_each(pending, outcome)
        next_try = pass_started + RETRY_INTERVAL
        if not to_retry or next_try > deadline:
            outcome.failed += to_retry
            return outcome
        log.warning(
            "%s failed, trying again at %s", _count(len(to_retry), "brief"), f"{next_try.astimezone(PRAGUE):%H:%M}"
        )
        sleep(max(next_try - clock(), timedelta()))
        pending = [f.subscription for f in to_retry]


@dataclass(frozen=True)
class _BriefSender:
    """Builds, sends, and records the brief of one subscription for the week."""

    source: BookingSource
    sent_briefs: SentBriefStore
    mail_sender: MailSender
    clock: Clock
    from_address: str
    working_hours: WorkingHours
    week: Week

    def try_each(self, subscriptions: list[Subscription], outcome: _Outcome) -> list[BriefFailure]:
        """Send each brief. One that fails does not stop the others.

        Adds the briefs that cannot work later to `outcome`, and returns the ones worth another try.
        """
        to_retry = []
        for subscription in subscriptions:
            try:
                self.send(subscription)
            except PERMANENT_ERRORS as error:
                log.error("Brief for %s failed, not trying again: %s", _describe(subscription), error)
                outcome.failed.append(BriefFailure(subscription, str(error)))
            except RETRYABLE_ERRORS as error:
                log.error("Brief for %s failed: %s", _describe(subscription), error)
                to_retry.append(BriefFailure(subscription, str(error)))
            except _NotRecorded as error:
                outcome.not_recorded.append(BriefFailure(subscription, str(error.__cause__)))
        return to_retry

    def send(self, subscription: Subscription) -> None:
        """Raises _NotRecorded when the brief went out but the record of sent briefs could not save it."""
        instrument = self.source.get_instrument(subscription.instrument_id)
        reservations = self.source.get_reservations(instrument.id, self.week)
        # Built now, not at the start of the run, so a late brief shows when it was really built.
        brief = build_brief(
            instrument, self.week, reservations, built_at=self.clock(), working_hours=self.working_hours
        )
        self.mail_sender.send(
            Message(
                from_address=self.from_address,
                to=subscription.email,
                subject=brief.subject,
                html=brief.html,
                text=brief.text,
            )
        )
        log.info("Sent brief for %s to %s", instrument.label, subscription.email)
        try:
            self.sent_briefs.record(subscription, self.week, self.clock())
        except sqlite3.Error as error:
            log.error(
                "Brief for %s sent, but not recorded: a re-run sends it again: %s", _describe(subscription), error
            )
            raise _NotRecorded from error


class _NotRecorded(Exception):
    """The brief went out, but the record of sent briefs could not save it. The cause holds the database error."""


def _send_error_report(mail_sender: MailSender, from_address: str, to: str, week: Week, outcome: _Outcome) -> None:
    """One error report to the service admin. When it fails too, the log is all there is."""
    text_parts, html_parts, subject_parts = [], [], []
    sections = (
        (
            outcome.failed,
            "not sent",
            "The Monday job could not send these briefs."
            " Fix the cause, then run the Monday job again: it sends only the briefs not sent yet.",
        ),
        (
            outcome.not_recorded,
            "sent but not recorded",
            "These briefs were sent but not recorded in the database, so a re-run sends them again.",
        ),
    )
    for failures, label, intro in sections:
        if not failures:
            continue
        lines = [f"{_describe(f.subscription)}: {f.error}" for f in failures]
        subject_parts.append(f"{_count(len(failures), 'brief')} {label}")
        text_parts.append("\n".join([intro, "", *(f"- {line}" for line in lines)]) + "\n")
        html_parts.append(f"<p>{escape(intro)}</p><ul>{''.join(f'<li>{escape(line)}</li>' for line in lines)}</ul>")
    subject = f"Nano Stand-up: {', '.join(subject_parts)} for the week of {_date(week)}"
    message = Message(
        from_address=from_address, to=to, subject=subject, html="".join(html_parts), text="\n".join(text_parts)
    )
    try:
        mail_sender.send(message)
    except OSError as error:
        log.error("Cannot send the error report to the service admin %s: %s", to, error)
        return
    log.info("Sent the error report to the service admin %s", to)


def _describe(subscription: Subscription) -> str:
    return f"instrument {subscription.instrument_id} to {subscription.email}"


def _date(week: Week) -> str:
    monday = week.monday
    return f"{monday.day}. {monday.month}. {monday.year}"


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"
