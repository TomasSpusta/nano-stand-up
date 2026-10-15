"""Commands for the service admin."""

from nano_standup.booking_source import BookingSource
from nano_standup.domain import Clock, require_guarantor
from nano_standup.subscriptions import Subscription, SubscriptionStore


def add_subscription(
    source: BookingSource, store: SubscriptionStore, instrument_id: str, email: str, clock: Clock
) -> Subscription:
    """Add an active subscription without the confirm link.

    The email must still be the guarantor email in the booking system, so a brief never goes to anyone else.
    """
    instrument = source.get_instrument(instrument_id)
    require_guarantor(instrument, email)
    return store.add_active(instrument.id, email, clock())
