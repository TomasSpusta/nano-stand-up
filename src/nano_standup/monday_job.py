"""Monday job: builds and sends this week's brief for every active subscription."""

import logging
from nano_standup.booking_source import BookingSource
from nano_standup.brief import build_brief
from nano_standup.domain import PRAGUE, Clock, Week
from nano_standup.mail import MailSender, Message
from nano_standup.subscriptions import SubscriptionStore

log = logging.getLogger(__name__)


def run_monday_job(
    source: BookingSource,
    subscriptions: SubscriptionStore,
    mail_sender: MailSender,
    clock: Clock,
    from_address: str,
) -> int:
    """Send the briefs and return how many went out. The week is the one that holds today in Prague."""
    now = clock()
    week = Week.containing(now.astimezone(PRAGUE).date())
    sent = 0
    for subscription in subscriptions.list_active():
        instrument = source.get_instrument(subscription.instrument_id)
        reservations = source.get_reservations(instrument.id, week)
        brief = build_brief(instrument, week, reservations, built_at=now)
        mail_sender.send(Message(from_address=from_address, to=subscription.email, subject=brief.subject, html=brief.html))
        log.info("Sent brief for %s to %s", instrument.label, subscription.email)
        sent += 1
    return sent
