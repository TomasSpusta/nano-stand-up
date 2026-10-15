"""Monday job: builds and sends this week's brief for every active subscription.

The server timer starts it once, at 08:00 Prague time.
A brief that fails is tried again at each RETRY_INTERVAL mark of the Prague clock until RETRY_DEADLINE,
unless a later try cannot fix the error.
What still fails then goes to the service admin in one error report (see `error_report`).
"""

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from nano_standup.booking_api import BookingAccessDenied, BookingApiError, BookingDataError
from nano_standup.booking_source import BookingSource, InstrumentNotFound
from nano_standup.brief import build_brief
from nano_standup.domain import PRAGUE, Clock, Instrument, Reservation, UserFacts, Week
from nano_standup.error_report import SendFailure, send_error_report
from nano_standup.formatting import counted, day_month_year
from nano_standup.heads_ups import HeadsUpSettings
from nano_standup.mail import MailSender, Message, RecipientRefused
from nano_standup.sent_briefs import SentBriefStore
from nano_standup.subscriptions import Subscription, SubscriptionStore

log = logging.getLogger(__name__)

# Waits for the given time. Passed in from outside, so tests can move a fake clock instead of waiting.
Sleep = Callable[[timedelta], None]

RETRY_INTERVAL = timedelta(minutes=15)
# Prague time, on the day of the run. Retries keep to the RETRY_INTERVAL marks of the Prague clock (08:15, 08:30, ...),
# so the last one is at this time. Then the service admin gets the error report.
RETRY_DEADLINE = time(10, 0)

# The failures one brief can have that a later try may not have: the booking API or the mail server is down.
# OSError covers all SMTP errors.
RETRYABLE_ERRORS = (BookingApiError, OSError)
# The failures a later try cannot fix, so the brief fails at once: the instrument is gone from the booking system,
# the API key has no access to it, it lacks the data a brief needs, or the mail server refuses the recipient address.
# Any other, unexpected error fails at once too.
PERMANENT_ERRORS = (InstrumentNotFound, BookingAccessDenied, BookingDataError, RecipientRefused)


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
    heads_up_settings: HeadsUpSettings,
) -> MondayJobResult:
    """Send the briefs not yet sent this week, with retries. The week is the one that holds today in Prague.

    A run started after the deadline, such as a manual re-run, tries each brief once.
    """
    started = clock().astimezone(PRAGUE)
    week = Week.containing(started.date())
    deadline = datetime.combine(started.date(), RETRY_DEADLINE, tzinfo=PRAGUE)
    sender = _BriefSender(source, sent_briefs, mail_sender, clock, from_address, heads_up_settings, week)
    active = subscriptions.list_active()
    to_send = [s for s in active if not sent_briefs.was_sent(s, week)]

    outcome = _send_with_retries(sender, to_send, clock, sleep, deadline)
    sent = len(to_send) - len(outcome.failed) - outcome.sent_by_another_run
    already_sent = len(active) - len(to_send) + outcome.sent_by_another_run
    if outcome.failed or outcome.not_recorded:
        send_error_report(
            mail_sender,
            from_address,
            service_admin_email,
            week,
            failed=outcome.failed,
            not_recorded=outcome.not_recorded,
        )
    log.info(
        "Monday job for the week of %s: %s processed, %s sent, %s already sent before, %s failed,"
        " %s sent but not recorded",
        day_month_year(week.monday),
        counted(len({s.instrument_id for s in to_send}), "instrument"),
        counted(sent, "brief"),
        counted(already_sent, "brief"),
        counted(len(outcome.failed), "brief"),
        counted(len(outcome.not_recorded), "brief"),
    )
    return MondayJobResult(
        sent=sent,
        already_sent=already_sent,
        failed=tuple(outcome.failed),
        not_recorded=tuple(outcome.not_recorded),
    )


@dataclass
class _Outcome:
    """What did not go as planned in one or more passes over the briefs."""

    failed: list[SendFailure] = field(default_factory=list)
    not_recorded: list[SendFailure] = field(default_factory=list)
    # Briefs another run sent after this run started, so this run did not send them.
    sent_by_another_run: int = 0


def _send_with_retries(
    sender: "_BriefSender", to_send: list[Subscription], clock: Clock, sleep: Sleep, deadline: datetime
) -> _Outcome:
    """Try the briefs, then try the ones that may work later again at each RETRY_INTERVAL mark, up to the deadline."""
    outcome = _Outcome()
    pending = to_send
    while True:
        to_retry = sender.try_each(pending, outcome)
        next_try = _next_mark(clock())
        if not to_retry or next_try > deadline:
            outcome.failed += to_retry
            return outcome
        log.warning(
            "%s failed, trying again at %s", counted(len(to_retry), "brief"), f"{next_try.astimezone(PRAGUE):%H:%M}"
        )
        sleep(max(next_try - clock(), timedelta()))
        pending = [f.subscription for f in to_retry]


def _next_mark(now: datetime) -> datetime:
    """The first RETRY_INTERVAL mark of the Prague clock after `now`, such as 08:15 for 08:00:05 or for 08:14.

    Marks, not intervals from the last try, so the last retry is at the deadline whatever second the job started,
    and a pass longer than the interval goes on at the next mark still ahead.
    """
    local = now.astimezone(PRAGUE)
    midnight = datetime.combine(local.date(), time(0, 0), tzinfo=PRAGUE)
    # Both share the PRAGUE tzinfo, so this is wall-clock time since midnight: the marks keep to the clock on
    # the days the clocks change.
    marks_passed = (local - midnight) // RETRY_INTERVAL
    return midnight + (marks_passed + 1) * RETRY_INTERVAL


@dataclass(frozen=True)
class _BriefSender:
    """Builds, sends, and records the brief of one subscription for the week."""

    source: BookingSource
    sent_briefs: SentBriefStore
    mail_sender: MailSender
    clock: Clock
    from_address: str
    heads_up_settings: HeadsUpSettings
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
                if not self.send(subscription, instrument):
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
            # An unexpected error, such as a bug on odd data, must not stop the other briefs or the error report.
            except Exception as error:  # Catch-all: for one brief, this loop is the top of the program.
                failure = SendFailure(subscription, instrument_name, f"{type(error).__name__}: {error}")
                log.exception("Brief for %s failed with an unexpected error, not trying again", failure.describe())
                outcome.failed.append(failure)
        return to_retry

    def send(self, subscription: Subscription, instrument: Instrument) -> bool:
        """Build, send, and record the brief. False when another run sent it meanwhile, so it was not sent again.

        Raises _NotRecorded when the brief went out but the record of sent briefs could not save it.
        """
        reservations = self.source.get_reservations(instrument.id, self.week)
        user_facts = _user_facts(self.source, instrument.id, reservations)
        # Built now, not at the start of the run, so a late brief shows when it was really built.
        brief = build_brief(
            instrument, self.week, reservations, user_facts, built_at=self.clock(), settings=self.heads_up_settings
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


def _user_facts(source: BookingSource, instrument_id: str, reservations: list[Reservation]) -> dict[str, UserFacts]:
    """Facts about each user of the week's reservations, by user id.

    Cancelled reservations need none: no heads-up rule about users looks at them.
    A reservation without a user id gets none either; the brief then treats its user as having no certificate.
    """
    user_ids = sorted({r.user_id for r in reservations if not r.is_cancelled and r.user_id})
    return {user_id: source.get_user_facts(instrument_id, user_id) for user_id in user_ids}
