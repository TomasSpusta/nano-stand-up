"""Web app: the page where a guarantor subscribes to an instrument's brief, the confirm link, and the unsubscribe
link in each brief.

The routes stay thin: the work is in `SubscriptionRequests`, and the HTML in `web_pages`.
"""

import logging
import urllib.parse
from typing import Annotated, assert_never

from fastapi import BackgroundTasks, FastAPI, Form, Query
from fastapi.responses import HTMLResponse

from nano_standup.booking_api import BookingApiError, BookingDataError
from nano_standup.booking_source import InstrumentNotFound
from nano_standup.domain import NotTheGuarantor
from nano_standup.subscription_request import SubscriptionRequests
from nano_standup.subscriptions import ConfirmLinkProblem, UnsubscribeLinkProblem
from nano_standup.web_pages import (
    FORM_PROBLEM,
    confirm_button_page,
    confirm_problem_page,
    confirm_unavailable_page,
    confirmed_page,
    form_page,
    not_the_guarantor_page,
    requested_page,
    unavailable_page,
    unsubscribe_button_page,
    unsubscribe_problem_page,
    unsubscribed_page,
)

log = logging.getLogger(__name__)


def link_problem_status(problem: ConfirmLinkProblem | UnsubscribeLinkProblem) -> int:
    # A match, not a dict, so the type checker fails when a new problem has no status.
    match problem:
        case ConfirmLinkProblem.EXPIRED | ConfirmLinkProblem.USED:
            return 410
        case ConfirmLinkProblem.UNKNOWN | UnsubscribeLinkProblem.UNKNOWN:
            return 404
        case UnsubscribeLinkProblem.ALREADY_UNSUBSCRIBED:
            # A normal answer, not an error: the guarantor gets what they asked for.
            return 200
        case _:
            assert_never(problem)


def unsubscribe_link_for(public_base_url: str, token: str) -> str:
    """The unsubscribe link in a brief. The web app answers it. `public_base_url` has no trailing slash."""
    return f"{public_base_url}/unsubscribe?{urllib.parse.urlencode({'token': token})}"


def create_app(requests: SubscriptionRequests) -> FastAPI:
    app = FastAPI(title="Nano Stand-up", docs_url=None, redoc_url=None, openapi_url=None)

    def request_in_background(instrument_id: str, email: str) -> None:
        """Runs after the page is sent, so the page and its timing are the same whether or not the email matched."""
        try:
            requests.request(instrument_id, email)
        except InstrumentNotFound as error:
            log.warning("Subscription request refused: %s", error)
        except Exception:  # Booking system, database, or mail errors: log them and keep the web service running.
            # The guarantor sees no email and can try again.
            log.exception("Subscription request for instrument %s failed", instrument_id)

    def form(problem: str = "", status_code: int = 200) -> HTMLResponse:
        try:
            listed = requests.instruments()
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
        return HTMLResponse(requested_page(requests.confirm_link_lifetime))

    @app.get("/confirm", response_class=HTMLResponse)
    def show_confirm(token: str = "") -> HTMLResponse:
        """Shows the Confirm button, or at once why the link cannot be used. It never uses the token."""
        found = requests.check(token)
        if isinstance(found, ConfirmLinkProblem):
            return HTMLResponse(confirm_problem_page(found), status_code=link_problem_status(found))
        return HTMLResponse(confirm_button_page(token))

    @app.post("/confirm", response_class=HTMLResponse)
    def confirm(token: Annotated[str, Form()] = "") -> HTMLResponse:
        try:
            confirmed = requests.confirm(token)
        except NotTheGuarantor as error:
            log.warning("Subscription not confirmed: %s", error)
            return HTMLResponse(not_the_guarantor_page(), status_code=403)
        except (BookingApiError, BookingDataError):
            log.exception("Cannot check the guarantor to confirm a subscription")
            return HTMLResponse(confirm_unavailable_page(), status_code=503)
        if isinstance(confirmed, ConfirmLinkProblem):
            return HTMLResponse(confirm_problem_page(confirmed), status_code=link_problem_status(confirmed))
        return HTMLResponse(confirmed_page())

    @app.get("/unsubscribe", response_class=HTMLResponse)
    def show_unsubscribe(token: str = "") -> HTMLResponse:
        """Shows the Unsubscribe button, or at once why the link cannot be used. It never uses the token."""
        found = requests.check_unsubscribe(token)
        if isinstance(found, UnsubscribeLinkProblem):
            return HTMLResponse(unsubscribe_problem_page(found), status_code=link_problem_status(found))
        return HTMLResponse(unsubscribe_button_page(token))

    @app.post("/unsubscribe", response_class=HTMLResponse)
    def unsubscribe(
        token: Annotated[str, Form()] = "",
        link_token: Annotated[str, Query(alias="token")] = "",
    ) -> HTMLResponse:
        """The button sends the token in the form. A mail app's one-click unsubscribe (RFC 8058) posts to the link
        itself, so the token is in the URL."""
        ended = requests.unsubscribe(token or link_token)
        if isinstance(ended, UnsubscribeLinkProblem):
            return HTMLResponse(unsubscribe_problem_page(ended), status_code=link_problem_status(ended))
        return HTMLResponse(unsubscribed_page())

    return app
