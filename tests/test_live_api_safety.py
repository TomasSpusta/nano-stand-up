import urllib.request
from pathlib import Path
from typing import Any

import pytest

from nano_standup.booking_api import BookingDataError, HttpBookingApi

from fake_booking_api import sem_json
from live_api_safety import failures_without_data, read_only
from local_booking_server import API_KEY, local_booking_server

URL = "http://127.0.0.1:9"
# Stands for personal data in raw booking JSON, which must never reach the test output.
PERSONAL = "Jana Example"


def _recording_urlopen(sent: list[str]) -> Any:  # Any: matches urllib.request.urlopen.
    def urlopen(request: urllib.request.Request, *args: Any, **kwargs: Any) -> str:
        sent.append(request.get_method())
        return "answer"

    return urlopen


@pytest.mark.parametrize(
    ("method", "path"),
    [("POST", "/api/equipment/x"), ("PUT", "/api/equipment/x"), ("DELETE", "/api/equipment/x"), ("PUT", "/api/login")],
)
def test_a_request_that_could_change_data_fails_before_it_is_sent(method: str, path: str) -> None:
    sent: list[str] = []
    guarded = read_only(_recording_urlopen(sent))

    with pytest.raises(pytest.fail.Exception, match=f"{method} {path} was refused"):
        guarded(urllib.request.Request(URL + path, data=b"{}", method=method))

    assert sent == []


def test_a_get_and_the_login_post_go_through() -> None:
    sent: list[str] = []
    guarded = read_only(_recording_urlopen(sent))

    guarded(urllib.request.Request(URL + "/api/equipment/x"))
    guarded(urllib.request.Request(URL + "/api/login", data=b"{}"))

    assert sent == ["GET", "POST"]


def test_the_real_http_layer_works_through_the_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", read_only(urllib.request.urlopen))
    with local_booking_server({"equip-sem": sem_json()}) as server:
        item = HttpBookingApi(server.url, API_KEY).get("/api/equipment/equip-sem", {})

    assert item["ge_alias"] == "TEST-SEM"
    assert server.logins == 1


def test_an_unexpected_error_fails_with_only_its_type(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).parent))
    pytester.makepyfile(
        f"""
        from live_api_safety import failures_without_data

        def test_it():
            raw = {{"firstname": {PERSONAL!r}}}
            with failures_without_data("Instrument i1, week of 2026-01-05"):
                raise AttributeError(str(raw))
        """
    )

    # --showlocals and a long traceback would show the raw JSON if a traceback were printed.
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", "--showlocals", "--tb=long")

    result.assert_outcomes(failed=1)
    output = result.stdout.str()
    assert "Instrument i1, week of 2026-01-05: AttributeError (its message is left out" in output
    assert PERSONAL not in output


def test_an_error_of_the_source_keeps_its_message() -> None:
    with (
        pytest.raises(pytest.fail.Exception, match="Instrument i1: BookingDataError: Reservation r1 has no 'id'"),
        failures_without_data("Instrument i1"),
    ):
        raise BookingDataError("Reservation r1 has no 'id'")


def test_no_error_passes_through() -> None:
    with failures_without_data("Instrument i1"):
        pass
