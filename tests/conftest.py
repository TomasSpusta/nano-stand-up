import locale
from collections.abc import Iterator

import pytest

# Runs a pytest run inside a test, to check what a failure prints.
pytest_plugins = ["pytester"]

# The names a Czech time language can have on Linux.
CZECH_TIME_LANGUAGES = ("cs_CZ.UTF-8", "cs_CZ.utf8")


@pytest.fixture
def czech_time_language() -> Iterator[None]:
    """Sets the server's time language to Czech for one test, so `%a` gives "út" and not "Tue"."""
    before = locale.setlocale(locale.LC_TIME)
    for name in CZECH_TIME_LANGUAGES:
        try:
            locale.setlocale(locale.LC_TIME, name)
            break
        except locale.Error:
            continue
    else:
        pytest.skip(f"No Czech time language installed: tried {', '.join(CZECH_TIME_LANGUAGES)}")
    try:
        yield
    finally:
        locale.setlocale(locale.LC_TIME, before)
