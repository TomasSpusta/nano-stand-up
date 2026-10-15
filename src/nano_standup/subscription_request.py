"""Subscription request: a guarantor asks for an instrument's brief, and gets a confirm link by email.

The link goes only to the guarantor email the booking system has for the instrument, never to the typed text.
"""

import html
import logging
import urllib.parse
from datetime import datetime, timedelta

from nano_standup.booking_source import BookingSource, InstrumentNotFound
from nano_standup.domain import (
    PRAGUE,
    Clock,
    Instrument,
    InstrumentName,
    NotTheGuarantor,
    require_guarantor,
)
from nano_standup.formatting import day_month_year, day_name
from nano_standup.instrument_list import InstrumentListCache
from nano_standup.mail import MailSender, Message
from nano_standup.subscriptions import ConfirmLinkProblem, Subscription, SubscriptionStore

log = logging.getLogger(__name__)

# While a confirm link sent this recently still works, no new one goes out for the same instrument,
# so the form cannot be used to flood a guarantor's inbox.
CONFIRM_MAIL_GAP = timedelta(minutes=5)


class SubscriptionRequests:
    """Handles requests for subscriptions and their confirm links. Built once, with what every request needs."""

    def __init__(
        self,
        source: BookingSource,
        store: SubscriptionStore,
        mail_sender: MailSender,
        clock: Clock,
        from_address: str,
        public_base_url: str,
    ) -> None:
        """`public_base_url` is the address guarantors open the web app at, such as https://standup.example.org,
        without a trailing slash. The confirm links in the emails start with it."""
        self._source = source
        self._instruments = InstrumentListCache(source, clock)
        self._store = store
        self._mail_sender = mail_sender
        self._clock = clock
        self._from_address = from_address
        self._public_base_url = public_base_url

    @property
    def confirm_link_lifetime(self) -> timedelta:
        return self._store.confirm_link_lifetime

    def instruments(self) -> list[InstrumentName]:
        """The instruments a guarantor can subscribe to. Raises BookingApiError or BookingDataError when there is
        no list at all."""
        return self._instruments.get()

    def request(self, instrument_id: str, email: str) -> None:
        """Send a confirm link when `email` is the instrument's guarantor email, ignoring case and surrounding spaces.

        Nothing is stored or sent when the instrument is not in the subscribable list, when the email does not
        match (names are never compared), or when a link for the instrument went out in the last few minutes.
        """
        if instrument_id not in {i.id for i in self._instruments.get()}:
            log.info("Subscription request for instrument %s, which is not in the subscribable list", instrument_id)
            return
        instrument = self._source.get_instrument(instrument_id)
        if not instrument.has_guarantor_email(email):
            # The typed email is not logged: it may belong to anyone.
            log.info("Subscription request for %s with an email that is not its guarantor email", instrument.label)
            return
        now = self._clock()
        if self._store.has_recent_request(instrument.id, since=now - CONFIRM_MAIL_GAP, now=now):
            log.info(
                "Subscription request for %s: a confirm link went out less than %s ago",
                instrument.label,
                CONFIRM_MAIL_GAP,
            )
            return
        requested = self._store.request(instrument.id, instrument.guarantor.email, now)
        link = f"{self._public_base_url}/confirm?{urllib.parse.urlencode({'token': requested.token})}"
        self._mail_sender.send(_confirm_mail(instrument, link, requested.expires_at, self._from_address))
        log.info("Sent a confirm link for %s to %s", instrument.label, instrument.guarantor.email)

    def check(self, token: str) -> Subscription | ConfirmLinkProblem:
        """The subscription the confirm link would make active now, or why it would not. Does not use the token."""
        return self._store.check(token, self._clock())

    def confirm(self, token: str) -> Subscription | ConfirmLinkProblem:
        """Make the requested subscription active and return it, or say why the link cannot be used.

        The email must still be the instrument's guarantor email in the booking system, as the guarantor may have
        changed since the link went out. Raises NotTheGuarantor when it is not, and BookingApiError or
        BookingDataError when the booking system cannot tell. In both cases the link is not used up.
        """
        found = self._store.check(token, self._clock())
        if isinstance(found, ConfirmLinkProblem):
            return found
        try:
            instrument = self._source.get_instrument(found.instrument_id)
        except InstrumentNotFound as error:
            raise NotTheGuarantor(f"{found.email!r} is no longer a guarantor email: {error}") from error
        require_guarantor(instrument, found.email)
        confirmed = self._store.confirm(token, self._clock())
        if isinstance(confirmed, Subscription):
            log.info("Subscription confirmed: %s gets the brief for %s", confirmed.email, confirmed.instrument_id)
        return confirmed


def _confirm_mail(instrument: Instrument, link: str, expires_at: datetime, from_address: str) -> Message:
    expires = expires_at.astimezone(PRAGUE)
    until = f"{day_name(expires.date())} {day_month_year(expires.date())} at {expires:%H:%M}"
    lines = [
        f"Someone asked for the weekly Nano Stand-up brief for {instrument.name} ({instrument.alias}).",
        "It goes to this address because the booking system lists it as the instrument's guarantor email.",
        "To start getting the brief every Monday, open this link and press Confirm:",
        link,
        f"The link works once, until {until} (Prague time).",
        "If you did not ask for this, ignore this email: nothing starts until someone presses Confirm.",
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
