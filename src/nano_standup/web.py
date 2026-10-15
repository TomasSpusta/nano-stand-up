"""Web app: the page where a guarantor subscribes to an instrument's brief, and the confirm link.

The routes stay thin: the work is in `SubscriptionRequests` and `SubscriptionStore`, and the HTML in `web_pages`.
"""

import logging
import sqlite3
import urllib.parse
from typing import Annotated, assert_never

from fastapi import BackgroundTasks, FastAPI, Form
from fastapi.responses import HTMLResponse

from nano_standup.booking_api import BookingApiError, BookingDataError
from nano_standup.booking_source import BookingSource, InstrumentNotFound
from nano_standup.domain import Clock
from nano_standup.instrument_list import InstrumentListCache
from nano_standup.mail import MailSender
from nano_standup.subscription_request import SubscriptionRequests
from nano_standup.subscriptions import ConfirmLinkProblem, SubscriptionStore
from nano_standup.web_pages import (
    FORM_PROBLEM,
    confirm_button_page,
    confirm_problem_page,
    confirmed_page,
    form_page,
    requested_page,
    unavailable_page,
)

log = logging.getLogger(__name__)


def confirm_problem_status(problem: ConfirmLinkProblem) -> int:
    # A match, not a dict, so the type checker fails when a new problem has no status.
    match problem:
        case ConfirmLinkProblem.EXPIRED | ConfirmLinkProblem.USED:
            return 410
        case ConfirmLinkProblem.UNKNOWN:
            return 404
        case _:
            assert_never(problem)


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
        found = store.check(token, clock())
        if isinstance(found, ConfirmLinkProblem):
            return HTMLResponse(confirm_problem_page(found), status_code=confirm_problem_status(found))
        return HTMLResponse(confirm_button_page(token))

    @app.post("/confirm", response_class=HTMLResponse)
    def confirm(token: Annotated[str, Form()] = "") -> HTMLResponse:
        confirmed = store.confirm(token, clock())
        if isinstance(confirmed, ConfirmLinkProblem):
            return HTMLResponse(confirm_problem_page(confirmed), status_code=confirm_problem_status(confirmed))
        log.info("Subscription confirmed: %s gets the brief for %s", confirmed.email, confirmed.instrument_id)
        return HTMLResponse(confirmed_page())

    return app
