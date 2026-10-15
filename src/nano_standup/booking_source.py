"""Booking source: the interface the rest of the service uses to read the booking system.

The real version is `booking_api.ApiBookingSource`. The fake version here serves hand-made data for tests.
"""

from typing import Protocol

from nano_standup.domain import Instrument, Reservation, Week


class InstrumentNotFound(LookupError):
    pass


class BookingSource(Protocol):
    def get_instrument(self, instrument_id: str) -> Instrument:
        """The instrument with its guarantor. Raises InstrumentNotFound when there is none."""
        ...

    def get_reservations(self, instrument_id: str, week: Week) -> list[Reservation]:
        """The reservations on the instrument that are inside the week, even only in part."""
        ...


class FakeBookingSource:
    def __init__(
        self,
        instruments: list[Instrument],
        reservations: dict[str, list[Reservation]] | None = None,
    ) -> None:
        self._instruments = {i.id: i for i in instruments}
        self._reservations = reservations or {}

    def get_instrument(self, instrument_id: str) -> Instrument:
        try:
            return self._instruments[instrument_id]
        except KeyError:
            raise InstrumentNotFound(f"No instrument with id {instrument_id!r}") from None

    def get_reservations(self, instrument_id: str, week: Week) -> list[Reservation]:
        return [r for r in self._reservations.get(instrument_id, []) if week.overlaps(r)]
