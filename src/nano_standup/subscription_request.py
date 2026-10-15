"""Subscription request: a guarantor asks for an instrument's brief, and gets a confirm link by email.

The link goes only to the guarantor email the booking system has for the instrument, never to the typed text.
"""

import html
import logging
from collections.abc import Callable
from datetime import datetime

from nano_standup.booking_source import BookingSource
from nano_standup.domain import PRAGUE, Clock, Instrument, day_name, normalize_email
from nano_standup.mail import MailSender, Message
from nano_standup.subscriptions import SubscriptionStore

log = logging.getLogger(__name__)


def request_subscription(
    source: BookingSource,
    store: SubscriptionStore,
    mail_sender: MailSender,
    clock: Clock,
    from_address: str,
    confirm_url: Callable[[str], str],
    instrument_id: str,
    email: str,
) -> None:
    """Send a confirm link when `email` is the instrument's guarantor email, ignoring case and surrounding spaces.

    When it is not, nothing is stored or sent. Names are never compared. `confirm_url` turns a token into a link.
    """
    instrument = source.get_instrument(instrument_id)
    if normalize_email(email) != normalize_email(instrument.guarantor.email):
        # The typed email is not logged: it may belong to anyone.
        log.info("Subscription request for %s with an email that is not its guarantor email", instrument.label)
        return
    pending = store.request(instrument.id, instrument.guarantor.email, clock())
    mail_sender.send(_confirm_mail(instrument, confirm_url(pending.token), pending.expires_at, from_address))
    log.info("Sent a confirm link for %s to %s", instrument.label, instrument.guarantor.email)


def _confirm_mail(instrument: Instrument, link: str, expires_at: datetime, from_address: str) -> Message:
    expires = expires_at.astimezone(PRAGUE)
    until = f"{day_name(expires.date())} {expires.day}. {expires.month}. {expires.year} at {expires:%H:%M}"
    lines = [
        f"Someone asked for the weekly Nano Stand-up brief for {instrument.name} ({instrument.alias}).",
        "It goes to this address because the booking system lists it as the instrument's guarantor email.",
        "To start getting the brief every Monday, open this link:",
        link,
        f"The link works once, until {until} (Prague time).",
        "If you did not ask for this, ignore this email: nothing starts without the link.",
    ]
    paragraphs = "".join(
        f'<p><a href="{html.escape(line)}">{html.escape(line)}</a></p>'
        if line == link
        else f"<p>{html.escape(line)}</p>"
        for line in lines
    )
    return Message(
        from_address=from_address,
        to=instrument.guarantor.email,
        subject=f"Confirm your Nano Stand-up brief for {instrument.alias}",
        html=f'<!doctype html><html><head><meta charset="utf-8"></head><body>{paragraphs}</body></html>',
        text="\n\n".join(lines) + "\n",
    )
