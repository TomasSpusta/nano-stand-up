"""The HTML pages of the web app. Every value from outside is escaped."""

from datetime import timedelta
from html import escape

from nano_standup.domain import InstrumentName
from nano_standup.subscriptions import ConfirmOutcome, UnsubscribeOutcome

STYLE = (
    "body{margin:0;padding:0 1rem;background:#f4f4f5;color:#1a1a1a;line-height:1.5;"
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

# Each says nothing about other subscriptions: whoever has the link sees only what the brief already showed.
UNSUBSCRIBE_MESSAGES = {
    UnsubscribeOutcome.UNSUBSCRIBED: (
        "Unsubscribed",
        "You are unsubscribed. No more of these weekly briefs will come. To get them again, subscribe again.",
    ),
    UnsubscribeOutcome.ALREADY_UNSUBSCRIBED: (
        "Already unsubscribed",
        "This subscription has already ended, so nothing changed. If you subscribed again since,"
        " use the unsubscribe link in your latest brief.",
    ),
    UnsubscribeOutcome.UNKNOWN: (
        "Link not valid",
        "This unsubscribe link is not valid. Check that you opened the whole link from the email.",
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


def confirm_button_page(token: str) -> str:
    """Opening the link only shows this page. Mail scanners open links too, so only the button uses the token."""
    return _page(
        "Confirm your subscription",
        "<p>Press Confirm to start getting the weekly brief for this instrument every Monday.</p>"
        '<form method="post" action="confirm">'
        f'<input type="hidden" name="token" value="{escape(token)}">'
        '<button type="submit">Confirm</button>'
        "</form>",
    )


def confirm_page(outcome: ConfirmOutcome) -> str:
    """The message for an outcome of `SubscriptionStore.confirm` or `check`, other than READY."""
    return _message_page(*CONFIRM_MESSAGES[outcome])


def unsubscribe_button_page(token: str) -> str:
    """Opening the link only shows this page. Mail scanners open links too, so only the button uses the token."""
    return _page(
        "Unsubscribe",
        "<p>Press Unsubscribe to stop getting this weekly brief. Only the brief you got this link in stops.</p>"
        '<form method="post" action="unsubscribe">'
        f'<input type="hidden" name="token" value="{escape(token)}">'
        '<button type="submit">Unsubscribe</button>'
        "</form>",
    )


def unsubscribe_page(outcome: UnsubscribeOutcome) -> str:
    """The message for an outcome of `SubscriptionStore.unsubscribe` or `check_unsubscribe`, other than READY."""
    return _message_page(*UNSUBSCRIBE_MESSAGES[outcome])


def unavailable_page() -> str:
    return _page(
        "Please try again later",
        "<p>This page cannot reach the booking system right now. Please try again in a few minutes.</p>",
    )


def _message_page(title: str, text: str) -> str:
    return _page(title, f"<p>{escape(text)}</p>")


def _page(title: str, body: str) -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)}</title><style>{STYLE}</style></head>"
        f"<body><main><h1>{escape(title)}</h1>{body}</main></body></html>"
    )
