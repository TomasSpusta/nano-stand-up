import dataclasses

from nano_standup.mail import Message, SmtpMailSender, SmtpSettings

from helpers import FakeSmtpServer

MESSAGE = Message(
    from_address="standup@example.org",
    to="hana.testova@example.org",
    subject="Brief: TEST-SEM",
    html="<p>Hi</p>",
    text="Hi",
)


def test_sends_plain_text_and_html_versions_without_login_or_tls(smtp: FakeSmtpServer) -> None:
    SmtpMailSender(SmtpSettings(host="localhost", port=1025), timeout_seconds=5).send(MESSAGE)

    [server] = smtp.connections
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


def test_starts_tls_then_logs_in_before_sending(smtp: FakeSmtpServer) -> None:
    settings = SmtpSettings(
        host="mail.example.org", port=587, username="standup", password="test-password", starttls=True
    )

    SmtpMailSender(settings).send(MESSAGE)

    [server] = smtp.connections
    assert server.calls == ["starttls", "login", "send", "quit"]
    assert server.login_args == ("standup", "test-password")


def test_logs_in_with_an_empty_password_when_none_is_set(smtp: FakeSmtpServer) -> None:
    SmtpMailSender(SmtpSettings(host="localhost", port=25, username="standup")).send(MESSAGE)

    [server] = smtp.connections
    assert server.calls == ["login", "send", "quit"]
    assert server.login_args == ("standup", "")


def test_a_message_with_an_unsubscribe_link_gets_the_one_click_unsubscribe_headers(smtp: FakeSmtpServer) -> None:
    link = "https://standup.example.org/unsubscribe?token=test-token"

    SmtpMailSender(SmtpSettings(host="localhost", port=1025)).send(dataclasses.replace(MESSAGE, unsubscribe_link=link))

    [email] = smtp.connections[0].sent
    assert email["List-Unsubscribe"] == f"<{link}>"
    assert email["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_a_message_without_an_unsubscribe_link_gets_no_unsubscribe_headers(smtp: FakeSmtpServer) -> None:
    SmtpMailSender(SmtpSettings(host="localhost", port=1025)).send(MESSAGE)

    [email] = smtp.connections[0].sent
    assert email["List-Unsubscribe"] is None
    assert email["List-Unsubscribe-Post"] is None
