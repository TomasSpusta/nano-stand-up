"""Mail sender: sends one message. SMTP for real use, in-memory for tests."""

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol


@dataclass(frozen=True)
class Message:
    sender: str
    to: str
    subject: str
    html: str


class MailSender(Protocol):
    def send(self, message: Message) -> None: ...


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


class SmtpMailSender:
    def __init__(self, settings: SmtpSettings, timeout_seconds: float = 30) -> None:
        self._settings = settings
        self._timeout = timeout_seconds

    def send(self, message: Message) -> None:
        email = EmailMessage()
        email["From"] = message.sender
        email["To"] = message.to
        email["Subject"] = message.subject
        email.set_content(message.html, subtype="html")
        settings = self._settings
        with smtplib.SMTP(settings.host, settings.port, timeout=self._timeout) as smtp:
            if settings.starttls:
                smtp.starttls()
            if settings.username:
                smtp.login(settings.username, settings.password or "")
            smtp.send_message(email)
