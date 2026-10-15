"""The HTML pages of the web app. Every value from outside is escaped."""

from datetime import timedelta
from html import escape

from nano_standup.domain import InstrumentName
from nano_standup.subscriptions import ConfirmOutcome

STYLE = (
    "body{margin:0;background:#f4f4f5;color:#1a1a1a;line-height:1.5;"
    "font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}"
    "main{max-width:36rem;margin:2rem auto;padding:1.5rem;background:#fff;border-radius:8px}"
    "h1{font-size:1.4rem;margin-top:0}"
    "label{display:block;margin-top:1rem;font-weight:600}"
    "select,input{width:100%;box-sizing:border-box;margin-top:.25rem;padding:.5rem;font-size:1rem}"
    "button{margin-top:1.25rem;padding:.6rem 1.2rem;font-size:1rem}"
    ".problem{color:#b91c1c}"
)
FORM_PROBLEM = "Pick an instrument and enter your email."
CONFIRM_MESSAGES = {
    ConfirmOutcome.CONFIRMED: (
        "Subscription confirmed",
        "Your subscription is active. The brief for this instrument comes every Monday morning.",
    ),
    ConfirmOutcome.EXPIRED: (
        "Link expired",
        "This confirm link has expired. Subscribe again to get a new one.",
    ),
    ConfirmOutcome.USED: (
        "Link already used",
        "This confirm link was already used. If it was you, your subscription is active."
        " To get a new link, subscribe again.",
    ),
    ConfirmOutcome.UNKNOWN: (
        "Link not valid",
        "This confirm link is not valid. Check that you opened the whole link from the email, or subscribe again.",
    ),
}


def form_page(instruments: list[InstrumentName], problem: str = "") -> str:
    """The page where a guarantor picks an instrument and enters their email."""
    options = "".join(
        f'<option value="{escape(i.id)}">{escape(i.alias)} - {escape(i.name)}</option>' for i in instruments
    )
    problem_html = f'<p class="problem">{escape(problem)}</p>' if problem else ""
    return _page(
        "Nano Stand-up brief",
        "<p>Get a weekly email every Monday about the reservations on an instrument you are the guarantor of.</p>"
        f"{problem_html}"
        '<form method="post">'
        '<label for="instrument_id">Instrument</label>'
        f'<select id="instrument_id" name="instrument_id" required><option value="">Pick an instrument</option>'
        f"{options}</select>"
        '<label for="email">Your email in the booking system</label>'
        '<input id="email" name="email" type="email" autocomplete="email" required>'
        '<button type="submit">Subscribe</button>'
        "</form>",
    )


def requested_page(confirm_link_lifetime: timedelta) -> str:
    """The same page whether or not the email matched, so it does not show who is whose guarantor."""
    hours = round(confirm_link_lifetime / timedelta(hours=1))
    lines = (
        "If this email is the instrument's guarantor email in the booking system, a confirm link is on its way to it.",
        f"Your subscription starts when you open that link. It works once, within {hours} hours.",
        "No email after a few minutes? Check that you entered the email the booking system has for you.",
    )
    return _page("Check your email", "".join(f"<p>{escape(line)}</p>" for line in lines))


def confirm_page(outcome: ConfirmOutcome) -> str:
    title, text = CONFIRM_MESSAGES[outcome]
    return _page(title, f"<p>{escape(text)}</p>")


def unavailable_page() -> str:
    return _page(
        "Please try again later",
        "<p>This page cannot reach the booking system right now. Please try again in a few minutes.</p>",
    )


def _page(title: str, body: str) -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)}</title><style>{STYLE}</style></head>"
        f"<body><main><h1>{escape(title)}</h1>{body}</main></body></html>"
    )
