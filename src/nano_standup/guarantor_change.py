"""Guarantor change: the weekly check that a subscriber is still the instrument's guarantor, and the stop notice
that tells a former subscriber why their brief stopped."""

from html import escape

from nano_standup.domain import Instrument, normalize_email
from nano_standup.mail import Message
from nano_standup.subscriptions import Subscription


def is_still_guarantor(subscription: Subscription, instrument: Instrument) -> bool:
    """True when the booking system still has the subscriber's email as the instrument's guarantor email."""
    return normalize_email(subscription.email) == normalize_email(instrument.guarantor.email)


def stop_notice(subscription: Subscription, instrument: Instrument, from_address: str) -> Message:
    """The stop notice to the subscriber's address. It never shows reservations or who the guarantor is now."""
    paragraphs = [
        f"You no longer get the weekly brief for {instrument.alias}.",
        f"The booking system no longer lists this email address as the guarantor of {instrument.alias},"
        " so your subscription has ended.",
        "If you are still the guarantor, subscribe again with the email address the booking system has for you.",
    ]
    return Message(
        from_address=from_address,
        to=subscription.email,
        subject=f"Your brief for {instrument.alias} has stopped",
        html="".join(f"<p>{escape(paragraph)}</p>" for paragraph in paragraphs),
        text="\n\n".join(paragraphs) + "\n",
    )
