"""Booking source: the interface the rest of the service uses to read the booking system.

The real version is `booking_api.ApiBookingSource`. The fake version here serves hand-made data for tests.
"""

from typing import Protocol

from nano_standup.domain import Certificate, Instrument, InstrumentName, Reservation, UserFacts, Week


class InstrumentNotFound(LookupError):
    pass


class BookingSource(Protocol):
    def list_subscribable_instruments(self) -> list[InstrumentName]:
        """The CF nano instruments that are in use, by alias. Booking resources that are people are left out."""
        ...

    def get_instrument(self, instrument_id: str) -> Instrument:
        """The instrument with its guarantor. Raises InstrumentNotFound when there is none."""
        ...

    def get_reservations(self, instrument_id: str, week: Week) -> list[Reservation]:
        """The reservations on the instrument that are inside the week, even only in part."""
        ...

    def get_user_facts(self, instrument_id: str, user_id: str) -> UserFacts:
        """The user's certificates for the instrument, valid or not, and when their reservations on it start.

        `user_id` is a Reservation.user_id.
        """
        ...


class FakeBookingSource:
    def __init__(
        self,
        instruments: list[Instrument],
        reservations: dict[str, list[Reservation]] | None = None,
        certificates: dict[str, dict[str, list[Certificate]]] | None = None,
    ) -> None:
        """`reservations` by instrument id. `certificates` by instrument id, then by user id.

        The reservations hold the users' history too: older reservations are outside the week.
        """
        self._instruments = {i.id: i for i in instruments}
        self._reservations = reservations or {}
        self._certificates = certificates or {}

    def list_subscribable_instruments(self) -> list[InstrumentName]:
        """All the fake's instruments, by alias."""
        instruments = sorted(self._instruments.values(), key=lambda i: i.alias.casefold())
        return [InstrumentName(i.id, i.name, i.alias) for i in instruments]

    def get_instrument(self, instrument_id: str) -> Instrument:
        try:
            return self._instruments[instrument_id]
        except KeyError:
            raise InstrumentNotFound(f"No instrument with id {instrument_id!r}") from None

    def get_reservations(self, instrument_id: str, week: Week) -> list[Reservation]:
        return [r for r in self._reservations.get(instrument_id, []) if week.overlaps(r)]

    def get_user_facts(self, instrument_id: str, user_id: str) -> UserFacts:
        return UserFacts(
            certificates=list(self._certificates.get(instrument_id, {}).get(user_id, [])),
            reservation_starts=[
                r.start
                for r in self._reservations.get(instrument_id, [])
                if r.user_id == user_id and not r.is_cancelled
            ],
        )
