"""Commands for the service admin."""

from datetime import datetime

from nano_standup.booking_source import BookingSource
from nano_standup.subscriptions import Subscription, SubscriptionStore, normalize_email


class NotTheGuarantor(ValueError):
    pass


def add_subscription(
    source: BookingSource, store: SubscriptionStore, instrument_id: str, email: str, now: datetime
) -> Subscription:
    """Add an active subscription without the confirm link.

    The email must still be the guarantor email in the booking system, so a brief never goes to anyone else.
    """
    instrument = source.get_instrument(instrument_id)
    if normalize_email(email) != normalize_email(instrument.guarantor.email):
        raise NotTheGuarantor(
            f"{email.strip()!r} is not the guarantor email of {instrument.label} in the booking system"
        )
    return store.add_active(instrument.id, email, now)
