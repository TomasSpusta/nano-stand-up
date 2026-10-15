"""Monday job: builds and sends this week's brief for every active subscription."""

import logging
from collections.abc import Callable

from nano_standup.booking_source import BookingSource
from nano_standup.brief import build_brief
from nano_standup.domain import PRAGUE, Clock, Week, WorkingHours
from nano_standup.mail import MailSender, Message
from nano_standup.subscriptions import SubscriptionStore

log = logging.getLogger(__name__)


def run_monday_job(
    source: BookingSource,
    subscriptions: SubscriptionStore,
    mail_sender: MailSender,
    clock: Clock,
    from_address: str,
    working_hours: WorkingHours,
    unsubscribe_link_for: Callable[[str], str],
) -> int:
    """Send the briefs and return how many went out. The week is the one that holds today in Prague.

    `unsubscribe_link_for` turns a token into the unsubscribe link of a brief.
    """
    now = clock()
    week = Week.containing(now.astimezone(PRAGUE).date())
    sent = 0
    for subscription in subscriptions.list_active():
        instrument = source.get_instrument(subscription.instrument_id)
        reservations = source.get_reservations(instrument.id, week)
        # The link's row is stored before the brief is sent. A failed send leaves a link nobody got, which is harmless.
        unsubscribe_link = unsubscribe_link_for(subscriptions.unsubscribe_token(subscription, now))
        brief = build_brief(instrument, week, reservations, now, working_hours, unsubscribe_link)
        mail_sender.send(
            Message(
                from_address=from_address,
                to=subscription.email,
                subject=brief.subject,
                html=brief.html,
                text=brief.text,
                unsubscribe_link=unsubscribe_link,
            )
        )
        log.info("Sent brief for %s to %s", instrument.label, subscription.email)
        sent += 1
    return sent
