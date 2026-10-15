"""Monday job: builds and sends this week's brief for every active subscription."""

import logging

from nano_standup.booking_source import BookingSource
from nano_standup.brief import build_brief
from nano_standup.domain import PRAGUE, Clock, Reservation, UserFacts, Week
from nano_standup.heads_ups import HeadsUpSettings
from nano_standup.mail import MailSender, Message
from nano_standup.subscriptions import SubscriptionStore

log = logging.getLogger(__name__)


def run_monday_job(
    source: BookingSource,
    subscriptions: SubscriptionStore,
    mail_sender: MailSender,
    clock: Clock,
    from_address: str,
    heads_up_settings: HeadsUpSettings,
) -> int:
    """Send the briefs and return how many went out. The week is the one that holds today in Prague."""
    now = clock()
    week = Week.containing(now.astimezone(PRAGUE).date())
    sent = 0
    for subscription in subscriptions.list_active():
        instrument = source.get_instrument(subscription.instrument_id)
        reservations = source.get_reservations(instrument.id, week)
        user_facts = _user_facts(source, instrument.id, reservations)
        brief = build_brief(instrument, week, reservations, user_facts, built_at=now, settings=heads_up_settings)
        mail_sender.send(
            Message(
                from_address=from_address,
                to=subscription.email,
                subject=brief.subject,
                html=brief.html,
                text=brief.text,
            )
        )
        log.info("Sent brief for %s to %s", instrument.label, subscription.email)
        sent += 1
    return sent


def _user_facts(source: BookingSource, instrument_id: str, reservations: list[Reservation]) -> dict[str, UserFacts]:
    """Facts about each user of the week's reservations, by user id.

    Cancelled reservations need none: no heads-up rule about users looks at them.
    A reservation without a user id gets none either; the brief then treats its user as having no certificate.
    """
    user_ids = sorted({r.user_id for r in reservations if r.is_not_cancelled and r.user_id})
    return {
        user_id: UserFacts(
            certificates=source.get_certificates(instrument_id, user_id),
            reservation_starts=source.get_reservation_starts(instrument_id, user_id),
        )
        for user_id in user_ids
    }
