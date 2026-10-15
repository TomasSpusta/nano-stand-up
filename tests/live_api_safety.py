"""Safety for checks against the live booking API: send only reads, and fail with no personal data in the output.

The booking data is personal and test output can end up in public logs.
A pytest traceback shows the source lines and, with --showlocals, the local values of each frame,
which can hold raw JSON with names, emails, and notes. So these failures have no traceback.
"""

import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from nano_standup.booking_api import LOGIN_PATH, BookingAccessDenied, BookingApiError, BookingDataError
from nano_standup.booking_source import InstrumentNotFound

UrlOpen = Callable[..., Any]  # Any: matches urllib.request.urlopen.
# The source's own errors. Their messages hold only ids, field names, times, and HTTP reasons, so they are shown.
ERRORS_WITHOUT_PERSONAL_DATA = (BookingApiError, BookingDataError, BookingAccessDenied, InstrumentNotFound)


def expect(ok: bool, message: str) -> None:
    """Fail with only `message`: no traceback, so no values of local variables reach the output."""
    if not ok:
        pytest.fail(message, pytrace=False)


@contextmanager
def failures_without_data(where: str) -> Iterator[None]:
    """Turn any error into a failure with no traceback. Other errors show only their type: they may hold raw JSON.

    pytest's own failures and skips are BaseExceptions, so they pass through as they are.
    """
    try:
        yield
    except ERRORS_WITHOUT_PERSONAL_DATA as error:
        message = f"{where}: {type(error).__name__}: {error}"
    except Exception as error:
        message = f"{where}: {type(error).__name__} (its message is left out: it may hold booking data)"
    else:
        return
    # From None: pytest would print the first error too, as "During handling of the above exception".
    raise pytest.fail.Exception(message, pytrace=False) from None


def read_only(urlopen: UrlOpen) -> UrlOpen:
    """`urlopen` that fails before sending anything but a GET or the login POST.

    It fails with pytest.fail, a BaseException, so the HTTP layer cannot catch it and go on.
    """

    def guarded(request: Any, *args: Any, **kwargs: Any) -> Any:  # Any: matches urllib.request.urlopen.
        if not isinstance(request, urllib.request.Request):
            pytest.fail("A request that is not a urllib Request was refused: its method is unknown", pytrace=False)
        method = request.get_method()
        path = urllib.parse.urlsplit(request.full_url).path
        if not (method == "GET" or (method == "POST" and path.endswith(LOGIN_PATH))):
            pytest.fail(f"{method} {path} was refused: only GET and the login are allowed", pytrace=False)
        return urlopen(request, *args, **kwargs)

    return guarded
