"""Test helpers and made-up fixtures. Never put real data from the booking system here: the repo is public."""

from datetime import datetime
from html.parser import HTMLParser

from nano_standup.domain import Guarantor, Instrument, Reservation, ReservationStatus

GUARANTOR = Guarantor(name="Hana Testova", email="hana.testova@example.org")
INSTRUMENT = Instrument(id="instr-1", name="Test Electron Microscope", alias="TEST-SEM", guarantor=GUARANTOR)


def reservation(
    start: str,
    end: str,
    user_name: str = "Petr Vymysleny",
    user_id: str | None = None,
    project_code: str = "2026-000001/RG-1-01",
    experiment_plan: str = "Imaging of samples",
    id: str | None = None,
    status: ReservationStatus = ReservationStatus.BOOKED,
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
