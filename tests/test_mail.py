import smtplib
from email.message import EmailMessage
from types import TracebackType

import pytest

from nano_standup.mail import Message, SmtpMailSender, SmtpSettings

MESSAGE = Message(
    from_address="standup@example.org",
    to="hana.testova@example.org",
    subject="Brief: TEST-SEM",
    html="<p>Hi</p>",
    text="Hi",
)


class FakeSmtp:
    """Stands in for smtplib.SMTP, the network edge, and records the calls in order."""

    instances: list["FakeSmtp"] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.connected_to = (host, port, timeout)
        self.calls: list[str] = []
        self.login_args: tuple[str, str] | None = None
        self.sent: list[EmailMessage] = []
        FakeSmtp.instances.append(self)

    def __enter__(self) -> "FakeSmtp":
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.calls.append("quit")

    def starttls(self) -> None:
        self.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append("login")
        self.login_args = (user, password)

    def send_message(self, message: EmailMessage) -> None:
        self.calls.append("send")
        self.sent.append(message)


@pytest.fixture
def smtp(monkeypatch: pytest.MonkeyPatch) -> list[FakeSmtp]:
    FakeSmtp.instances = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSmtp)
    return FakeSmtp.instances


def test_sends_plain_text_and_html_versions_without_login_or_tls(smtp: list[FakeSmtp]) -> None:
    SmtpMailSender(SmtpSettings(host="localhost", port=1025), timeout_seconds=5).send(MESSAGE)

    [server] = smtp
    assert server.connected_to == ("localhost", 1025, 5)
    assert server.calls == ["send", "quit"]
    [email] = server.sent
    assert email["From"] == "standup@example.org"
    assert email["To"] == "hana.testova@example.org"
    assert email["Subject"] == "Brief: TEST-SEM"
    assert email.get_content_type() == "multipart/alternative"
    plain, rich = email.iter_parts()
    # Mail clients show the last version they can read, so HTML goes last.
    assert (plain.get_content_type(), plain.get_content().strip()) == ("text/plain", "Hi")
    assert (rich.get_content_type(), rich.get_content().strip()) == ("text/html", "<p>Hi</p>")


def test_starts_tls_then_logs_in_before_sending(smtp: list[FakeSmtp]) -> None:
    settings = SmtpSettings(
        host="mail.example.org", port=587, username="standup", password="test-password", starttls=True
    )

    SmtpMailSender(settings).send(MESSAGE)

    [server] = smtp
    assert server.calls == ["starttls", "login", "send", "quit"]
    assert server.login_args == ("standup", "test-password")


def test_logs_in_with_an_empty_password_when_none_is_set(smtp: list[FakeSmtp]) -> None:
    SmtpMailSender(SmtpSettings(host="localhost", port=25, username="standup")).send(MESSAGE)

    [server] = smtp
    assert server.calls == ["login", "send", "quit"]
    assert server.login_args == ("standup", "")
