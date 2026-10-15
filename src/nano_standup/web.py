"""Web app: the page where a guarantor subscribes to an instrument's brief, the confirm link, and the unsubscribe
link in each brief.

The routes stay thin: the work is in `SubscriptionRequests` and `SubscriptionStore`, and the HTML in `web_pages`.
"""

import logging
import sqlite3
import urllib.parse
from typing import Annotated

from fastapi import BackgroundTasks, FastAPI, Form, Query
from fastapi.responses import HTMLResponse

from nano_standup.booking_api import BookingApiError, BookingDataError
from nano_standup.booking_source import BookingSource, InstrumentNotFound
from nano_standup.domain import Clock
from nano_standup.instrument_list import InstrumentListCache
from nano_standup.mail import MailSender
from nano_standup.subscription_request import SubscriptionRequests
from nano_standup.subscriptions import ConfirmOutcome, SubscriptionStore, UnsubscribeOutcome
from nano_standup.web_pages import (
    FORM_PROBLEM,
    confirm_button_page,
    confirm_page,
    form_page,
    requested_page,
    unavailable_page,
    unsubscribe_button_page,
    unsubscribe_page,
)

log = logging.getLogger(__name__)

CONFIRM_STATUS = {
    ConfirmOutcome.CONFIRMED: 200,
    ConfirmOutcome.READY: 200,
    ConfirmOutcome.EXPIRED: 410,
    ConfirmOutcome.USED: 410,
    ConfirmOutcome.UNKNOWN: 404,
}
# "Already unsubscribed" is a normal answer, not an error: the guarantor gets what they asked for.
UNSUBSCRIBE_STATUS = {
    UnsubscribeOutcome.UNSUBSCRIBED: 200,
    UnsubscribeOutcome.READY: 200,
    UnsubscribeOutcome.ALREADY_UNSUBSCRIBED: 200,
    UnsubscribeOutcome.UNKNOWN: 404,
}


def unsubscribe_url(public_base_url: str, token: str) -> str:
    """The unsubscribe link in a brief. The web app answers it."""
    return f"{public_base_url.rstrip('/')}/unsubscribe?{urllib.parse.urlencode({'token': token})}"


def create_app(
    source: BookingSource,
    store: SubscriptionStore,
    mail_sender: MailSender,
    clock: Clock,
    from_address: str,
    public_base_url: str,
) -> FastAPI:
    """`public_base_url` is the address guarantors open the app at, such as https://standup.example.org.
    The confirm links in the emails start with it."""
    base_url = public_base_url.rstrip("/")
    app = FastAPI(title="Nano Stand-up", docs_url=None, redoc_url=None, openapi_url=None)
    instruments = InstrumentListCache(source, clock)

    def confirm_url(token: str) -> str:
        return f"{base_url}/confirm?{urllib.parse.urlencode({'token': token})}"

    requests = SubscriptionRequests(source, instruments, store, mail_sender, clock, from_address, confirm_url)

    def request_in_background(instrument_id: str, email: str) -> None:
        """Runs after the page is sent, so the page and its timing are the same whether or not the email matched."""
        try:
            requests.request(instrument_id, email)
        except InstrumentNotFound as error:
            log.warning("Subscription request refused: %s", error)
        except (BookingApiError, BookingDataError, sqlite3.Error, OSError):
            # OSError covers the SMTP errors. The guarantor sees no email and can try again.
            log.exception("Subscription request for instrument %s failed", instrument_id)
        except Exception:  # Last resort: log any other failure here, and keep the web service running.
            log.exception("Subscription request for instrument %s failed", instrument_id)

    def form(problem: str = "", status_code: int = 200) -> HTMLResponse:
        try:
            listed = instruments.get()
        except (BookingApiError, BookingDataError):
            log.exception("Cannot list the instruments for the subscribe page")
            return HTMLResponse(unavailable_page(), status_code=503)
        return HTMLResponse(form_page(listed, problem), status_code=status_code)

    @app.get("/", response_class=HTMLResponse)
    def show_form() -> HTMLResponse:
        return form()

    @app.post("/", response_class=HTMLResponse)
    def subscribe(
        background: BackgroundTasks,
        instrument_id: Annotated[str, Form()] = "",
        email: Annotated[str, Form()] = "",
    ) -> HTMLResponse:
        if not instrument_id.strip() or not email.strip():
            return form(FORM_PROBLEM, status_code=400)
        background.add_task(request_in_background, instrument_id.strip(), email)
        return HTMLResponse(requested_page(store.confirm_link_lifetime))

    @app.get("/confirm", response_class=HTMLResponse)
    def show_confirm(token: str = "") -> HTMLResponse:
        """Shows the Confirm button, or at once why the link cannot be used. It never uses the token."""
        outcome = store.check(token, clock())
        page = confirm_button_page(token) if outcome is ConfirmOutcome.READY else confirm_page(outcome)
        return HTMLResponse(page, status_code=CONFIRM_STATUS[outcome])

    @app.post("/confirm", response_class=HTMLResponse)
    def confirm(token: Annotated[str, Form()] = "") -> HTMLResponse:
        result = store.confirm(token, clock())
        if result.subscription:
            log.info(
                "Subscription confirmed: %s gets the brief for %s",
                result.subscription.email,
                result.subscription.instrument_id,
            )
        return HTMLResponse(confirm_page(result.outcome), status_code=CONFIRM_STATUS[result.outcome])

    @app.get("/unsubscribe", response_class=HTMLResponse)
    def show_unsubscribe(token: str = "") -> HTMLResponse:
        """Shows the Unsubscribe button, or at once why the link cannot be used. It never uses the token."""
        outcome = store.check_unsubscribe(token)
        page = unsubscribe_button_page(token) if outcome is UnsubscribeOutcome.READY else unsubscribe_page(outcome)
        return HTMLResponse(page, status_code=UNSUBSCRIBE_STATUS[outcome])

    @app.post("/unsubscribe", response_class=HTMLResponse)
    def unsubscribe(
        token: Annotated[str, Form()] = "",
        link_token: Annotated[str, Query(alias="token")] = "",
    ) -> HTMLResponse:
        """The button sends the token in the form. A mail app's one-click unsubscribe (RFC 8058) posts to the link
        itself, so the token is in the URL."""
        result = store.unsubscribe(token or link_token, clock())
        if result.subscription:
            log.info(
                "Subscription ended: %s unsubscribed from the brief for %s",
                result.subscription.email,
                result.subscription.instrument_id,
            )
        return HTMLResponse(unsubscribe_page(result.outcome), status_code=UNSUBSCRIBE_STATUS[result.outcome])

    return app
