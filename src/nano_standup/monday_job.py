"""Monday job: builds and sends this week's brief for every active subscription.

Before each brief it checks that the subscriber is still the instrument's guarantor.
If not, the subscription ends and the subscriber gets a short stop notice instead of the brief.

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
from nano_standup.domain import PRAGUE, Clock, Instrument, Week, WorkingHours
from nano_standup.guarantor_change import is_still_guarantor, stop_notice
from nano_standup.mail import MailSender, Message
from nano_standup.sent_briefs import SentBriefStore
from nano_standup.subscriptions import Subscription, SubscriptionStore

log = logging.getLogger(__name__)

# Waits for the given time. Passed in from outside, so tests can move a fake clock instead of waiting.
Sleep = Callable[[timedelta], None]

RETRY_INTERVAL = timedelta(minutes=15)
# Prague time. The last try is at this time or just before it; then the service admin gets the error report.
RETRY_DEADLINE = time(10, 0)

# The failures one brief can have that a later try may not have: the booking API or the mail server is down.
# OSError covers all SMTP errors.
RETRYABLE_ERRORS = (BookingApiError, OSError)
# The failures a later try cannot fix, so the brief fails at once: the instrument is gone from the booking system,
# or it lacks the data a brief needs. Any other, unexpected error fails at once too.
PERMANENT_ERRORS = (InstrumentNotFound, BookingDataError)


@dataclass(frozen=True)
class SendFailure:
    """A brief or a stop notice that the job could not send or record."""

    subscription: Subscription
    # The instrument's alias and id when the job could read it, or else only its id.
    instrument: str
    # The error message of the last try.
    error: str

    def describe(self) -> str:
        return f"{self.instrument} to {self.subscription.email}"


@dataclass(frozen=True)
class MondayJobResult:
    # Briefs sent by this run, recorded or not.
    sent: int
    # Briefs a run before already sent this week, so this run skipped them.
    already_sent: int
    # Briefs still not sent at the end of the run.
    failed: tuple[SendFailure, ...]
    # Briefs sent, but the record of sent briefs could not save them, so a re-run sends them again.
    not_recorded: tuple[SendFailure, ...]
    # Subscriptions ended because the subscriber is no longer the instrument's guarantor.
    ended: int
    # Stop notices about an ended subscription that could not be sent. The subscription ended anyway.
    stop_notices_not_sent: tuple[SendFailure, ...]


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
    step = _SubscriptionStep(
        source=source,
        subscriptions=subscriptions,
        sent_briefs=sent_briefs,
        mail_sender=mail_sender,
        clock=clock,
        from_address=from_address,
        working_hours=working_hours,
        week=week,
    )
    active = subscriptions.list_active()
    to_send = [s for s in active if not sent_briefs.was_sent(s, week)]

    outcome = _send_with_retries(step, to_send, clock, sleep, deadline)
    sent = (
        len(to_send) - len(outcome.failed) - outcome.sent_by_another_run - outcome.ended - outcome.ended_by_another_run
    )
    already_sent = len(active) - len(to_send) + outcome.sent_by_another_run
    if outcome.failed or outcome.not_recorded or outcome.stop_notices_not_sent:
        _send_error_report(mail_sender, from_address, service_admin_email, week, outcome)
    log.info(
        "Monday job for the week of %s: %s processed, %s sent, %s already sent before, %s failed,"
        " %s sent but not recorded, %s ended because the guarantor changed",
        _date(week),
        _count(len({s.instrument_id for s in to_send}), "instrument"),
        _count(sent, "brief"),
        _count(already_sent, "brief"),
        _count(len(outcome.failed), "brief"),
        _count(len(outcome.not_recorded), "brief"),
        _count(outcome.ended, "subscription"),
    )
    return MondayJobResult(
        sent=sent,
        already_sent=already_sent,
        failed=tuple(outcome.failed),
        not_recorded=tuple(outcome.not_recorded),
        ended=outcome.ended,
        stop_notices_not_sent=tuple(outcome.stop_notices_not_sent),
    )


@dataclass
class _Outcome:
    """What did not go as planned in one or more passes over the briefs."""

    failed: list[SendFailure] = field(default_factory=list)
    not_recorded: list[SendFailure] = field(default_factory=list)
    # Briefs another run sent after this run started, so this run did not send them.
    sent_by_another_run: int = 0
    # Subscriptions this run ended because the subscriber is no longer the guarantor, so no brief went out.
    ended: int = 0
    # Subscriptions another run ended after this run started, so this run sent no brief and no stop notice.
    ended_by_another_run: int = 0
    stop_notices_not_sent: list[SendFailure] = field(default_factory=list)


def _send_with_retries(
    step: "_SubscriptionStep", to_send: list[Subscription], clock: Clock, sleep: Sleep, deadline: datetime
) -> _Outcome:
    """Try the briefs, then try the ones that may work later again every RETRY_INTERVAL, up to the deadline."""
    outcome = _Outcome()
    pending = to_send
    while True:
        pass_started = clock()
        to_retry = step.try_each(pending, outcome)
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
class _SubscriptionStep:
    """One subscription's step of the run: check the guarantor, then build, send, and record the brief for the week.

    When the subscriber is no longer the guarantor, it ends the subscription and sends the stop notice instead.
    """

    source: BookingSource
    subscriptions: SubscriptionStore
    sent_briefs: SentBriefStore
    mail_sender: MailSender
    clock: Clock
    from_address: str
    working_hours: WorkingHours
    week: Week

    def try_each(self, subscriptions: list[Subscription], outcome: _Outcome) -> list[SendFailure]:
        """Send each brief. One that fails does not stop the others.

        Adds the briefs that cannot work later to `outcome`, and returns the ones worth another try.
        """
        to_retry = []
        for subscription in subscriptions:
            instrument_name = subscription.instrument_id
            try:
                instrument = self.source.get_instrument(subscription.instrument_id)
                instrument_name = instrument.label
                if not is_still_guarantor(subscription, instrument):
                    self.end(subscription, instrument, outcome)
                elif not self.send(subscription, instrument):
                    outcome.sent_by_another_run += 1
            except PERMANENT_ERRORS as error:
                failure = SendFailure(subscription, instrument_name, str(error))
                log.error("Brief for %s failed, not trying again: %s", failure.describe(), error)
                outcome.failed.append(failure)
            except RETRYABLE_ERRORS as error:
                failure = SendFailure(subscription, instrument_name, str(error))
                log.error("Brief for %s failed: %s", failure.describe(), error)
                to_retry.append(failure)
            except _NotRecorded as error:
                outcome.not_recorded.append(SendFailure(subscription, instrument_name, str(error.__cause__)))
            # Catch-all: "Catch what you expect" allows one where it keeps the service running. Here it keeps
            # an unexpected error, such as a bug on odd data, from stopping the other briefs and the error report.
            except Exception as error:
                failure = SendFailure(subscription, instrument_name, f"{type(error).__name__}: {error}")
                log.exception("Brief for %s failed with an unexpected error, not trying again", failure.describe())
                outcome.failed.append(failure)
        return to_retry

    def end(self, subscription: Subscription, instrument: Instrument, outcome: _Outcome) -> None:
        """End the subscription and send the stop notice instead of the brief.

        The subscription ends before the stop notice is sent, so one that fails never lets a brief through later.
        A stop notice that fails is not tried again: it goes into the error report,
        so the service admin can tell the person.
        An ended subscription is not active, so no later run checks it or sends the stop notice again.
        """
        if not self.subscriptions.end_because_guarantor_changed(subscription, self.clock()):
            log.info(
                "Subscription for %s to %s was ended by another run meanwhile", instrument.label, subscription.email
            )
            outcome.ended_by_another_run += 1
            return
        outcome.ended += 1
        log.warning(
            "Subscription for %s to %s ended: the booking system no longer lists this email as the guarantor",
            instrument.label,
            subscription.email,
        )
        try:
            self.mail_sender.send(stop_notice(subscription, instrument, self.from_address))
        except OSError as error:
            failure = SendFailure(subscription, instrument.label, str(error))
            log.error("Cannot send the stop notice for %s: %s", failure.describe(), error)
            outcome.stop_notices_not_sent.append(failure)
            return
        # Catch-all, as for the briefs: an unexpected mail error must not stop the other briefs and the error report.
        except Exception as error:
            failure = SendFailure(subscription, instrument.label, f"{type(error).__name__}: {error}")
            log.exception("Cannot send the stop notice for %s: unexpected error", failure.describe())
            outcome.stop_notices_not_sent.append(failure)
            return
        log.info("Sent the stop notice for %s to %s", instrument.label, subscription.email)

    def send(self, subscription: Subscription, instrument: Instrument) -> bool:
        """Build, send, and record the brief. False when another run sent it meanwhile, so it was not sent again.

        Raises _NotRecorded when the brief went out but the record of sent briefs could not save it.
        """
        reservations = self.source.get_reservations(instrument.id, self.week)
        # Built now, not at the start of the run, so a late brief shows when it was really built.
        brief = build_brief(
            instrument, self.week, reservations, built_at=self.clock(), working_hours=self.working_hours
        )
        # Checked again just before sending: another run may have sent it since this run started.
        if self.sent_briefs.was_sent(subscription, self.week):
            log.info("Brief for %s to %s was sent by another run meanwhile", instrument.label, subscription.email)
            return False
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
                "Brief for %s to %s sent, but not recorded: a re-run sends it again: %s",
                instrument.label,
                subscription.email,
                error,
            )
            raise _NotRecorded from error
        return True


class _NotRecorded(Exception):
    """The brief went out, but the record of sent briefs could not save it. The cause holds the database error."""


@dataclass(frozen=True)
class _ReportSection:
    """One kind of failure in the error report."""

    failures: list[SendFailure]
    # What failed, such as "brief", and what went wrong, such as "not sent", for the subject.
    noun: str
    label: str
    # The sentence before the list of failures.
    intro: str


def _send_error_report(mail_sender: MailSender, from_address: str, to: str, week: Week, outcome: _Outcome) -> None:
    """One error report to the service admin. When it fails too, the log is all there is."""
    text_parts, html_parts, subject_parts = [], [], []
    sections = (
        _ReportSection(
            outcome.failed,
            "brief",
            "not sent",
            "The Monday job could not send these briefs."
            " Fix the cause, then run the Monday job again: it sends only the briefs not sent yet.",
        ),
        _ReportSection(
            outcome.not_recorded,
            "brief",
            "sent but not recorded",
            "These briefs were sent but not recorded in the database, so a re-run sends them again.",
        ),
        _ReportSection(
            outcome.stop_notices_not_sent,
            "stop notice",
            "not sent",
            "These subscriptions ended because the subscriber is no longer the guarantor, and no brief went to them."
            " The stop notice telling them why could not be sent, and the Monday job does not try it again.",
        ),
    )
    for section in sections:
        if not section.failures:
            continue
        lines = [f"{f.describe()}: {f.error}" for f in section.failures]
        subject_parts.append(f"{_count(len(section.failures), section.noun)} {section.label}")
        text_parts.append("\n".join([section.intro, "", *(f"- {line}" for line in lines)]) + "\n")
        html_parts.append(
            f"<p>{escape(section.intro)}</p><ul>{''.join(f'<li>{escape(line)}</li>' for line in lines)}</ul>"
        )
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


def _date(week: Week) -> str:
    monday = week.monday
    return f"{monday.day}. {monday.month}. {monday.year}"


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"
