"""The HTML pages of the web app. Every value from outside is escaped."""

from datetime import timedelta
from html import escape
from typing import assert_never

from nano_standup.domain import InstrumentName, name_repeats_alias
from nano_standup.subscriptions import ConfirmLinkProblem, UnsubscribeLinkProblem

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
# Relative, so it also works when a proxy serves the app under a path. All pages are at the top level of the app.
SUBSCRIBE_LINK = '<p><a href="./">Subscribe again</a></p>'


def form_page(instruments: list[InstrumentName], problem: str = "") -> str:
    """The page where a guarantor picks an instrument and enters their email."""
    options = "".join(f'<option value="{escape(i.id)}">{escape(_picker_label(i))}</option>' for i in instruments)
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


def _picker_label(instrument: InstrumentName) -> str:
    """Alias and name, or the alias alone when the name only repeats it."""
    if name_repeats_alias(instrument):
        return instrument.alias
    return f"{instrument.alias} - {instrument.name}"


def requested_page(confirm_link_lifetime: timedelta) -> str:
    """The same page whether or not the email matched, so it does not show who is whose guarantor."""
    hours = round(confirm_link_lifetime / timedelta(hours=1))
    lines = (
        "If this email is the instrument's guarantor email in the booking system, a confirm link is on its way to it.",
        f"Your subscription starts when you open that link and press Confirm. It works once, within {hours} hours.",
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


def confirmed_page() -> str:
    return _page(
        "Subscription confirmed",
        "<p>Your subscription is active. The brief for this instrument comes every Monday morning.</p>",
    )


def not_the_guarantor_page() -> str:
    """It does not say who the guarantor is now."""
    return _page(
        "Subscription not started",
        "<p>This email is no longer the instrument's guarantor email in the booking system,"
        " so the subscription did not start.</p>"
        "<p>If you are the guarantor, check the email the booking system has for you and subscribe again.</p>"
        + SUBSCRIBE_LINK,
    )


def confirm_unavailable_page() -> str:
    return _page(
        "Please try again later",
        "<p>Your subscription is not confirmed yet: this page cannot reach the booking system right now.</p>"
        "<p>Your confirm link still works. Please open it again in a few minutes.</p>",
    )


def confirm_problem_page(problem: ConfirmLinkProblem) -> str:
    """Why the confirm link cannot be used."""
    # Each of them says to subscribe again.
    return _message_page(*_confirm_problem_message(problem), subscribe_link=True)


def _confirm_problem_message(problem: ConfirmLinkProblem) -> tuple[str, str]:
    # A match, not a dict, so the type checker fails when a new problem has no message.
    match problem:
        case ConfirmLinkProblem.EXPIRED:
            return "Link expired", "This confirm link has expired. Subscribe again to get a new one."
        case ConfirmLinkProblem.USED:
            return (
                "Link already used",
                "This confirm link was already used. If it was you, your subscription is active."
                " To get a new link, subscribe again.",
            )
        case ConfirmLinkProblem.UNKNOWN:
            return (
                "Link not valid",
                "This confirm link is not valid."
                " Check that you opened the whole link from the email, or subscribe again.",
            )
        case _:
            assert_never(problem)


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


# The unsubscribe pages say nothing about other subscriptions: whoever has the link sees only what the brief
# already showed.
def unsubscribed_page() -> str:
    return _message_page(
        "Unsubscribed",
        "You are unsubscribed. No more of these weekly briefs will come. To get them again, subscribe again.",
        subscribe_link=True,
    )


def unsubscribe_problem_page(problem: UnsubscribeLinkProblem) -> str:
    """Why the unsubscribe link cannot end a subscription."""
    return _message_page(
        *_unsubscribe_problem_message(problem), subscribe_link=problem is UnsubscribeLinkProblem.GUARANTOR_CHANGED
    )


def _unsubscribe_problem_message(problem: UnsubscribeLinkProblem) -> tuple[str, str]:
    # A match, not a dict, so the type checker fails when a new problem has no message.
    match problem:
        case UnsubscribeLinkProblem.ALREADY_UNSUBSCRIBED:
            return (
                "Already unsubscribed",
                "This subscription has already ended, so nothing changed. If you subscribed again since,"
                " use the unsubscribe link in your latest brief.",
            )
        case UnsubscribeLinkProblem.GUARANTOR_CHANGED:
            # Never says who the guarantor is now.
            return (
                "Already unsubscribed",
                "This subscription has already ended, so nothing changed: the booking system no longer lists this"
                " email address as the instrument's guarantor. If you are still the guarantor, subscribe again with"
                " the email address the booking system has for you.",
            )
        case UnsubscribeLinkProblem.UNKNOWN:
            return (
                "Link not valid",
                "This unsubscribe link is not valid. Check that you opened the whole link from the email.",
            )
        case _:
            assert_never(problem)


def error_page() -> str:
    """For an error nobody expected. The service admin finds it in the log."""
    return _page("Something went wrong", "<p>This page has a problem right now. Please try again in a few minutes.</p>")


def unavailable_page() -> str:
    return _page(
        "Please try again later",
        "<p>This page cannot reach the booking system right now. Please try again in a few minutes.</p>",
    )


def _message_page(title: str, text: str, subscribe_link: bool = False) -> str:
    """A page with one message. `subscribe_link` for a message that says to subscribe again."""
    return _page(title, f"<p>{escape(text)}</p>{SUBSCRIBE_LINK if subscribe_link else ''}")


def _page(title: str, body: str) -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)}</title><style>{STYLE}</style></head>"
        f"<body><main><h1>{escape(title)}</h1>{body}</main></body></html>"
    )
