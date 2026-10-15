"""Monday job: builds and sends this week's brief for every active subscription."""

import logging
from dataclasses import dataclass

from nano_standup.booking_source import BookingSource
from nano_standup.brief import build_brief
from nano_standup.domain import PRAGUE, Clock, Week, WorkingHours
from nano_standup.mail import MailSender, Message
from nano_standup.sent_briefs import SentBriefStore
from nano_standup.subscriptions import Subscription, SubscriptionStore

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MondayJobResult:
    # Briefs sent by this run.
    sent: int
    # Briefs a run before already sent this week, so this run skipped them.
    already_sent: int


def run_monday_job(
    source: BookingSource,
    subscriptions: SubscriptionStore,
    sent_briefs: SentBriefStore,
    mail_sender: MailSender,
    clock: Clock,
    from_address: str,
    working_hours: WorkingHours,
) -> MondayJobResult:
    """Send the briefs not yet sent this week. The week is the one that holds today in Prague."""
    week = Week.containing(clock().astimezone(PRAGUE).date())
    sender = _BriefSender(source, sent_briefs, mail_sender, clock, from_address, working_hours, week)
    active = subscriptions.list_active()
    to_send = [s for s in active if not sent_briefs.was_sent(s, week)]
    for subscription in to_send:
        sender.send(subscription)
    return MondayJobResult(sent=len(to_send), already_sent=len(active) - len(to_send))


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

    def send(self, subscription: Subscription) -> None:
        instrument = self.source.get_instrument(subscription.instrument_id)
        reservations = self.source.get_reservations(instrument.id, self.week)
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
