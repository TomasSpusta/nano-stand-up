"""Error report: the one message to the service admin when the Monday job ends with briefs not sent or not recorded."""

import logging
from dataclasses import dataclass
from html import escape

from nano_standup.domain import Week
from nano_standup.formatting import counted, day_month_year
from nano_standup.mail import MailSender, Message, RecipientRefused
from nano_standup.subscriptions import Subscription

log = logging.getLogger(__name__)

NOT_SENT_INTRO = (
    "The Monday job could not send these briefs."
    " Fix the cause, then run the Monday job again: it sends only the briefs not sent yet."
)
NOT_RECORDED_INTRO = "These briefs were sent but not recorded in the database, so a re-run sends them again."


@dataclass(frozen=True)
class SendFailure:
    """One brief that was not sent, or was sent but not recorded."""

    subscription: Subscription
    # The instrument's alias and id when the job could read it, or else only its id.
    instrument_name: str
    # The error message of the last try.
    error: str

    def describe(self) -> str:
        return f"{self.instrument_name} to {self.subscription.email}"


def send_error_report(
    mail_sender: MailSender,
    from_address: str,
    to: str,
    week: Week,
    *,
    failed: list[SendFailure],
    not_recorded: list[SendFailure],
) -> None:
    """Send one error report to the service admin. When it fails too, the log is all there is."""
    try:
        mail_sender.send(_error_report(from_address, to, week, failed, not_recorded))
    except (OSError, RecipientRefused) as error:
        log.error("Cannot send the error report to the service admin %s: %s", to, error)
        return
    log.info("Sent the error report to the service admin %s", to)


def _error_report(
    from_address: str, to: str, week: Week, failed: list[SendFailure], not_recorded: list[SendFailure]
) -> Message:
    text_parts, html_parts, subject_parts = [], [], []
    sections = ((failed, "not sent", NOT_SENT_INTRO), (not_recorded, "sent but not recorded", NOT_RECORDED_INTRO))
    for failures, label, intro in sections:
        if not failures:
            continue
        lines = [f"{f.describe()}: {f.error}" for f in failures]
        subject_parts.append(f"{counted(len(failures), 'brief')} {label}")
        text_parts.append("\n".join([intro, "", *(f"- {line}" for line in lines)]) + "\n")
        html_parts.append(f"<p>{escape(intro)}</p><ul>{''.join(f'<li>{escape(line)}</li>' for line in lines)}</ul>")
    subject = f"Nano Stand-up: {', '.join(subject_parts)} for the week of {day_month_year(week.monday)}"
    return Message(
        from_address=from_address, to=to, subject=subject, html="".join(html_parts), text="\n".join(text_parts)
    )
