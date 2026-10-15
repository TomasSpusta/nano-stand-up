"""Monday job: builds and sends this week's brief for every active subscription.

The server timer starts it once, at 08:00 Prague time.
A brief that fails is tried again every RETRY_INTERVAL until RETRY_DEADLINE.
What still fails then goes to the service admin in one error report.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
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
BRIEF_ERRORS = (BookingApiError, BookingDataError, InstrumentNotFound, OSError)


@dataclass(frozen=True)
class BriefFailure:
    subscription: Subscription
    # The error message of the last try.
    error: str


@dataclass(frozen=True)
class MondayJobResult:
    # Briefs sent by this run.
    sent: int
    # Briefs a run before already sent this week, so this run skipped them.
    already_sent: int
    # Briefs still not sent at the end of the run.
    failed: tuple[BriefFailure, ...]


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

    pending = to_send
    while True:
        pass_started = clock()
        failed = sender.try_each(pending)
        next_try = pass_started + RETRY_INTERVAL
        if not failed or next_try > deadline:
            break
        log.warning(
            "%s failed, trying again at %s", _count(len(failed), "brief"), f"{next_try.astimezone(PRAGUE):%H:%M}"
        )
        sleep(max(next_try - clock(), timedelta()))
        pending = [f.subscription for f in failed]
    sent = len(to_send) - len(failed)

    if failed:
        _alert_service_admin(mail_sender, from_address, service_admin_email, week, failed)
    log.info(
        "Monday job for the week of %s: %s processed, %s sent, %d already sent before, %d failed",
        _date(week),
        _count(len({s.instrument_id for s in to_send}), "instrument"),
        _count(sent, "brief"),
        len(active) - len(to_send),
        len(failed),
    )
    return MondayJobResult(sent=sent, already_sent=len(active) - len(to_send), failed=tuple(failed))


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

    def try_each(self, subscriptions: list[Subscription]) -> list[BriefFailure]:
        """Send each brief. One that fails does not stop the others. Returns the ones that failed."""
        failed = []
        for subscription in subscriptions:
            try:
                self.send(subscription)
            except BRIEF_ERRORS as error:
                log.error("Brief for %s failed: %s", _describe(subscription), error)
                failed.append(BriefFailure(subscription, str(error)))
        return failed

    def send(self, subscription: Subscription) -> None:
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
        self.sent_briefs.record(subscription, self.week, self.clock())
        log.info("Sent brief for %s to %s", instrument.label, subscription.email)


def _alert_service_admin(
    mail_sender: MailSender, from_address: str, to: str, week: Week, failed: list[BriefFailure]
) -> None:
    """One error report that lists the briefs not sent. When it fails too, the log is all there is."""
    lines = [f"{_describe(f.subscription)}: {f.error}" for f in failed]
    intro = (
        f"The Monday job could not send these briefs by {RETRY_DEADLINE:%H:%M} Prague time."
        " Fix the cause, then run the Monday job again: it sends only the briefs not sent yet."
    )
    text = "\n".join([intro, "", *(f"- {line}" for line in lines)]) + "\n"
    html = f"<p>{escape(intro)}</p><ul>{''.join(f'<li>{escape(line)}</li>' for line in lines)}</ul>"
    subject = f"Nano Stand-up: {_count(len(failed), 'brief')} not sent for the week of {_date(week)}"
    try:
        mail_sender.send(Message(from_address=from_address, to=to, subject=subject, html=html, text=text))
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
