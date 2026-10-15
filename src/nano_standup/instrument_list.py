"""The list of instruments a guarantor can subscribe to, kept in memory.

Reading it from the booking API takes a few seconds, so the web app keeps it for a while.
"""

import logging
import threading
from datetime import datetime, timedelta

from nano_standup.booking_api import BookingApiError, BookingDataError
from nano_standup.booking_source import BookingSource
from nano_standup.domain import Clock, InstrumentName

log = logging.getLogger(__name__)

# How long a read list is used before it is read again. New or retired instruments are rare.
INSTRUMENT_LIST_MAX_AGE = timedelta(minutes=30)


class InstrumentListCache:
    def __init__(self, source: BookingSource, clock: Clock) -> None:
        self._source = source
        self._clock = clock
        self._instruments: list[InstrumentName] | None = None
        self._read_at: datetime | None = None
        # The web app serves requests in threads. One list read at a time is enough.
        self._lock = threading.Lock()

    def get(self) -> list[InstrumentName]:
        """The list, read again when it is older than the max age.

        When a read fails, an older list is used. With no list at all, the BookingApiError or BookingDataError
        is raised.
        """
        with self._lock:
            now = self._clock()
            fresh = self._read_at is not None and now - self._read_at < INSTRUMENT_LIST_MAX_AGE
            if fresh and self._instruments is not None:
                return self._instruments
            try:
                self._instruments = self._source.list_subscribable_instruments()
                self._read_at = now
            except (BookingApiError, BookingDataError) as error:
                if self._instruments is None:
                    raise
                log.warning("Cannot read the instrument list, using the older instrument list: %s", error)
            return self._instruments
