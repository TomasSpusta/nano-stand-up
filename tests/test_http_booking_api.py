import pytest

from nano_standup.booking_api import BookingApiError, BookingDataError, HttpBookingApi

from fake_booking_api import sem_json
from local_booking_server import API_KEY, local_booking_server

SEM = sem_json()


def test_logs_in_once_and_sends_list_filters_as_repeated_parameters() -> None:
    with local_booking_server({"equip-sem": SEM}) as server:
        api = HttpBookingApi(server.url + "/api/", API_KEY)

        first = api.get("/api/equipment/equip-sem", {"filters[]": ["a:eq:1", "b:eq:2"], "limit": 50})
        api.get("/api/equipment/equip-sem", {"scope": "ge_ownerid"})

    assert first["ge_alias"] == "TEST-SEM"
    assert server.logins == 1
    assert server.queries[0] == {"filters[]": ["a:eq:1", "b:eq:2"], "limit": ["50"]}


def test_an_http_error_names_the_request_and_status() -> None:
    with local_booking_server({}) as server:
        api = HttpBookingApi(server.url, API_KEY)

        with pytest.raises(BookingApiError, match="/api/equipment/nope failed: HTTP 404") as error:
            api.get("/api/equipment/nope", {})

    assert error.value.status == 404


def test_a_wrong_api_key_fails_at_login() -> None:
    with local_booking_server({}) as server:
        api = HttpBookingApi(server.url, "wrong-key")

        with pytest.raises(BookingApiError, match="/api/login failed: HTTP 401"):
            api.get("/api/equipment/equip-sem", {})


def test_an_unreachable_api_names_the_request() -> None:
    with local_booking_server({}) as server:
        url = server.url
    # The server is stopped now, so nothing answers on its port.
    api = HttpBookingApi(url, API_KEY)

    with pytest.raises(BookingApiError, match="/api/login failed: .*refused") as error:
        api.get("/api/equipment/equip-sem", {})

    assert error.value.status is None


def test_a_login_answer_without_a_token_names_the_request() -> None:
    with local_booking_server({}, login_answer={}) as server:
        api = HttpBookingApi(server.url, API_KEY)

        with pytest.raises(BookingDataError, match="/api/login.*accessToken"):
            api.get("/api/equipment/equip-sem", {})
