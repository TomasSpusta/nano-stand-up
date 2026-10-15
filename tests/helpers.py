"""Test helpers and made-up fixtures. Never put real data from the booking system here: the repo is public."""

from datetime import datetime, time
from email.message import EmailMessage
from html.parser import HTMLParser
from types import TracebackType

from nano_standup.domain import Guarantor, Instrument, Reservation, ReservationStatus, WorkingHours

GUARANTOR = Guarantor(name="Hana Testova", email="hana.testova@example.org")
INSTRUMENT = Instrument(id="instr-1", name="Test Electron Microscope", alias="TEST-SEM", guarantor=GUARANTOR)
WORKING_HOURS = WorkingHours(time(7, 0), time(19, 0))
UNSUBSCRIBE_LINK = "https://standup.example.org/unsubscribe?token=test-token"


def reservation(
    start: str,
    end: str,
    user_name: str = "Petr Vymysleny",
    user_id: str | None = None,
    project_code: str = "2026-000001/RG-1-01",
    experiment_plan: str = "Imaging of samples",
    id: str | None = None,
    status: ReservationStatus = ReservationStatus.RESERVED,
    note: str = "",
    private_note: str = "",
) -> Reservation:
    return Reservation(
        id=id or f"res-{start}",
        start=datetime.fromisoformat(start),
        end=datetime.fromisoformat(end),
        user_id=f"user-{user_name}" if user_id is None else user_id,
        user_name=user_name,
        project_code=project_code,
        experiment_plan=experiment_plan,
        status=status,
        note=note,
        private_note=private_note,
    )


class _TextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def visible_text(html: str) -> str:
    """The text a reader sees, with runs of whitespace collapsed to one space."""
    collector = _TextCollector()
    collector.feed(html)
    return " ".join(" ".join(collector.parts).split())


def heads_up_text(text: str, html: str) -> str:
    """The Heads-up section of the plain-text version, on one line. Checks that the HTML version shows the same."""
    lines = text.splitlines()
    start = lines.index("HEADS-UP")
    section = " ".join(" ".join(lines[start + 1 : lines.index("", start)]).split())
    assert f"Heads-up {section}" in visible_text(html)
    return section


class FakeSmtpConnection:
    """One connection to the fake SMTP server. Records the calls in order."""

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.connected_to = (host, port, timeout)
        self.calls: list[str] = []
        self.login_args: tuple[str, str] | None = None
        self.sent: list[EmailMessage] = []

    def __enter__(self) -> "FakeSmtpConnection":
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


class FakeSmtpServer:
    """Stands in for the mail server, the network edge. `connect` takes the place of smtplib.SMTP."""

    def __init__(self) -> None:
        self.connections: list[FakeSmtpConnection] = []

    def connect(self, host: str, port: int, timeout: float) -> FakeSmtpConnection:
        connection = FakeSmtpConnection(host, port, timeout)
        self.connections.append(connection)
        return connection

    @property
    def sent(self) -> list[EmailMessage]:
        return [message for connection in self.connections for message in connection.sent]
