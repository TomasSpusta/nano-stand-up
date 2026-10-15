"""Test helpers and made-up fixtures. Never put real data from the booking system here: the repo is public."""

import smtplib
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from email.message import EmailMessage
from html.parser import HTMLParser
from types import TracebackType

from nano_standup.domain import (
    Certificate,
    Guarantor,
    Instrument,
    Reservation,
    ReservationStatus,
    UserFacts,
    WorkingHours,
)
from nano_standup.heads_ups import HeadsUpSettings

# The user id follows the scheme of `reservation()`, so `reservation(..., user_name="Hana Testova")` is hers.
GUARANTOR_USER_ID = "user-Hana Testova"
GUARANTOR = Guarantor(name="Hana Testova", email="hana.testova@example.org", user_id=GUARANTOR_USER_ID)
INSTRUMENT = Instrument(id="instr-1", name="Test Electron Microscope", alias="TEST-SEM", guarantor=GUARANTOR)
WORKING_HOURS = WorkingHours(time(7, 0), time(19, 0))
UNSUBSCRIBE_LINK = "https://standup.example.org/unsubscribe?token=test-token"
HEADS_UP_SETTINGS = HeadsUpSettings(working_hours=WORKING_HOURS, new_user_reservations=3)
# Valid from long ago and never ends.
OLD_CERTIFICATE = Certificate(valid_from=date(2020, 1, 1), valid_to=None)
# Many reservations long ago, so a user with them is not a new user.
OLD_RESERVATION_STARTS = [datetime(2021, 1, 4, 9, 0) + timedelta(days=i) for i in range(10)]


def experienced_users(reservations: Iterable[Reservation]) -> dict[str, UserFacts]:
    """Each user holds an old certificate and has many reservations before, so no heads-up rule about users matches."""
    return {r.user_id: UserFacts([OLD_CERTIFICATE], OLD_RESERVATION_STARTS) for r in reservations}


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

    def __init__(self, server: "FakeSmtpServer", host: str, port: int, timeout: float) -> None:
        self._server = server
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
        if self._server.refusal is not None:
            raise smtplib.SMTPRecipientsRefused({str(message["To"]): self._server.refusal})
        self.sent.append(message)


class FakeSmtpServer:
    """Stands in for the mail server, the network edge. `connect` takes the place of smtplib.SMTP."""

    def __init__(self) -> None:
        self.connections: list[FakeSmtpConnection] = []
        # The server's answer to every recipient address, as smtplib gives it, or None to take them.
        self.refusal: tuple[int, bytes] | None = None

    def connect(self, host: str, port: int, timeout: float) -> FakeSmtpConnection:
        connection = FakeSmtpConnection(self, host, port, timeout)
        self.connections.append(connection)
        return connection

    @property
    def sent(self) -> list[EmailMessage]:
        return [message for connection in self.connections for message in connection.sent]
