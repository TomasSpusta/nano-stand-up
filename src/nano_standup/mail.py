"""Mail sender: sends one message. SMTP for real use, in-memory for tests."""

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from email.policy import default
from typing import Protocol


@dataclass(frozen=True)
class Message:
    from_address: str
    to: str
    subject: str
    html: str
    # The same content for mail clients that do not show HTML.
    text: str
    # The message's unsubscribe link, if any. Mail apps show their own unsubscribe button for it.
    unsubscribe_link: str | None = None


class RecipientRefused(RuntimeError):
    """The mail server refused the recipient address for good (a 5xx answer), so trying again does not help."""


class MailSender(Protocol):
    def send(self, message: Message) -> None:
        """Raises RecipientRefused when the mail server refuses the recipient for good, or OSError on other errors."""
        ...


class InMemoryMailSender:
    def __init__(self) -> None:
        self.sent: list[Message] = []

    def send(self, message: Message) -> None:
        self.sent.append(message)


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    username: str | None = None
    password: str | None = None
    starttls: bool = False


# Python folds a header longer than 78 characters. A link has no spaces to fold at, so Python would encode the
# List-Unsubscribe header instead, and mail apps would not see a link there. 998 is the line limit of RFC 5322.
_POLICY = default.clone(max_line_length=998)


class SmtpMailSender:
    def __init__(self, settings: SmtpSettings, timeout_seconds: float = 30) -> None:
        self._settings = settings
        self._timeout = timeout_seconds

    def send(self, message: Message) -> None:
        email = EmailMessage(policy=_POLICY)
        email["From"] = message.from_address
        email["To"] = message.to
        email["Subject"] = message.subject
        if message.unsubscribe_link:
            # RFC 8058 one-click unsubscribe: the mail app posts "List-Unsubscribe=One-Click" to the link.
            email["List-Unsubscribe"] = f"<{message.unsubscribe_link}>"
            email["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        # Mail clients show the last version they can read, so HTML goes last.
        email.set_content(message.text)
        email.add_alternative(message.html, subtype="html")
        settings = self._settings
        with smtplib.SMTP(settings.host, settings.port, timeout=self._timeout) as smtp:
            if settings.starttls:
                smtp.starttls()
            if settings.username:
                smtp.login(settings.username, settings.password or "")
            try:
                smtp.send_message(email)
            except smtplib.SMTPRecipientsRefused as error:
                answers = list(error.recipients.values())
                # A 4xx answer, such as greylisting, may pass later, so it stays an SMTP error worth another try.
                if all(500 <= code < 600 for code, _ in answers):
                    detail = "; ".join(f"{code} {reply.decode(errors='replace')}" for code, reply in answers)
                    raise RecipientRefused(f"The mail server refused {message.to}: {detail}") from error
                raise
