"""The whole brief, compared with saved copies, so a change to the brief code cannot change what the guarantor gets by accident.

When a change to the brief is on purpose, write new copies with `UPDATE_GOLDEN=1 uv run pytest tests/test_brief_golden.py`
and check the diff of the files in tests/golden/.
"""

import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from nano_standup.brief import Brief, build_brief
from nano_standup.domain import Reservation, ReservationStatus, Week

from helpers import INSTRUMENT, reservation

GOLDEN = Path(__file__).parent / "golden"
WEEK = Week(date(2026, 10, 19))
BUILT_AT = datetime(2026, 10, 19, 8, 0, tzinfo=ZoneInfo("Europe/Prague"))

# One of each thing a brief can show.
FULL_WEEK = [
    reservation("2026-10-19T08:30", "2026-10-19T11:00", user_name="Petr Vymysleny", note="Bring own holder"),
    reservation(
        "2026-10-19T14:00",
        "2026-10-19T14:45",
        user_name="Jana Smyslena",
        experiment_plan="",
        private_note="Paid by the group\nCheck the invoice",
    ),
    reservation("2026-10-20T15:00", "2026-10-21T13:00", user_name="Karel Neexistujici", status=ReservationStatus.REQUESTED),
    reservation(
        "2026-10-21T09:00",
        "2026-10-21T10:00",
        user_name="Eva <Testova>",
        user_id="",
        status=ReservationStatus.CANCELLED,
        note="Imaging of samples",
    ),
    reservation("2026-10-24T10:00", "2026-11-02T10:00", user_name="Petr Vymysleny", status=ReservationStatus.REQUESTED),
]


def _brief(reservations: list[Reservation]) -> Brief:
    return build_brief(INSTRUMENT, WEEK, reservations, BUILT_AT)


def _assert_same_as_saved(name: str, actual: str) -> None:
    path = GOLDEN / name
    if os.environ.get("UPDATE_GOLDEN"):
        path.write_text(actual, encoding="utf-8")
    assert actual == path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("name", "reservations"),
    [("full_week", FULL_WEEK), ("empty_week", [])],
)
def test_the_brief_is_the_same_as_the_saved_copy(name: str, reservations: list[Reservation]) -> None:
    brief = _brief(reservations)

    _assert_same_as_saved(f"{name}.html", brief.html)
    _assert_same_as_saved(f"{name}.txt", brief.text)
