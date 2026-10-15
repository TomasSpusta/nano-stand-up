"""Web app: the page where a guarantor subscribes to an instrument's brief, and the confirm link.

The routes stay thin: the work is in `subscription_request` and `SubscriptionStore`, and the HTML in `web_pages`.
"""

import logging
import sqlite3
import urllib.parse
from typing import Annotated

from fastapi import BackgroundTasks, FastAPI, Form
from fastapi.responses import HTMLResponse

from nano_standup.booking_api import BookingApiError, BookingDataError
from nano_standup.booking_source import BookingSource, InstrumentNotFound
from nano_standup.domain import Clock
from nano_standup.mail import MailSender
from nano_standup.subscription_request import request_subscription
from nano_standup.subscriptions import ConfirmOutcome, SubscriptionStore
from nano_standup.web_pages import FORM_PROBLEM, confirm_page, form_page, requested_page, unavailable_page

log = logging.getLogger(__name__)

CONFIRM_STATUS = {
    ConfirmOutcome.CONFIRMED: 200,
    ConfirmOutcome.EXPIRED: 410,
    ConfirmOutcome.USED: 410,
    ConfirmOutcome.UNKNOWN: 404,
}


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

    def confirm_url(token: str) -> str:
        return f"{base_url}/confirm?{urllib.parse.urlencode({'token': token})}"

    def request_in_background(instrument_id: str, email: str) -> None:
        """Runs after the page is sent, so the page and its timing are the same whether or not the email matched."""
        try:
            request_subscription(source, store, mail_sender, clock, from_address, confirm_url, instrument_id, email)
        except InstrumentNotFound as error:
            log.warning("Subscription request refused: %s", error)
        except (BookingApiError, BookingDataError, sqlite3.Error, OSError):
            # OSError covers the SMTP errors. The guarantor sees no email and can try again.
            log.exception("Subscription request for instrument %s failed", instrument_id)

    def form(problem: str = "", status_code: int = 200) -> HTMLResponse:
        try:
            instruments = source.list_subscribable_instruments()
        except (BookingApiError, BookingDataError):
            log.exception("Cannot list the instruments for the subscribe page")
            return HTMLResponse(unavailable_page(), status_code=503)
        return HTMLResponse(form_page(instruments, problem), status_code=status_code)

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
    def confirm(token: str = "") -> HTMLResponse:
        result = store.confirm(token, clock())
        if result.subscription:
            log.info(
                "Subscription confirmed: %s gets the brief for %s",
                result.subscription.email,
                result.subscription.instrument_id,
            )
        return HTMLResponse(confirm_page(result.outcome), status_code=CONFIRM_STATUS[result.outcome])

    return app
