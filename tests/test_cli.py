from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nano_standup.cli import main
from nano_standup.subscriptions import Subscription, SubscriptionStore

from fake_booking_api import sem_json
from local_booking_server import API_KEY, local_booking_server

SEM = sem_json()


def write_config(tmp_path: Path, booking_url: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(
        f"BOOKING_API_BASE_URL={booking_url}\nBOOKING_API_KEY={API_KEY}\n"
        "SMTP_HOST=127.0.0.1\nSMTP_PORT=1\nMAIL_FROM=standup@example.org\nDATABASE_PATH=data/standup.sqlite3\n"
    )
    return path


def test_add_subscription_stores_the_guarantor_subscription(tmp_path: Path) -> None:
    with local_booking_server({"equip-sem": SEM}) as server:
        config = write_config(tmp_path, server.url)
        code = main(["--config", str(config), "add-subscription", "equip-sem", "Hana.Testova@example.org"])

    assert code == 0
    assert SubscriptionStore(tmp_path / "data/standup.sqlite3").list_active() == [
        Subscription("equip-sem", "hana.testova@example.org")
    ]


def test_add_subscription_refuses_another_email_with_exit_code_2(tmp_path: Path) -> None:
    with local_booking_server({"equip-sem": SEM}) as server:
        config = write_config(tmp_path, server.url)
        code = main(["--config", str(config), "add-subscription", "equip-sem", "someone@example.org"])

    assert code == 2
    assert SubscriptionStore(tmp_path / "data/standup.sqlite3").list_active() == []


def test_a_missing_config_file_exits_with_code_2(tmp_path: Path) -> None:
    assert main(["--config", str(tmp_path / "missing.env"), "run-monday-job"]) == 2


def test_an_unexpected_failure_exits_with_code_1(tmp_path: Path) -> None:
    with local_booking_server({"equip-sem": SEM}) as server:
        config = write_config(tmp_path, server.url)
        main(["--config", str(config), "add-subscription", "equip-sem", "hana.testova@example.org"])
    # The server is stopped now, so the Monday job cannot reach the booking API.

    assert main(["--config", str(config), "run-monday-job"]) == 1


def test_serve_runs_the_web_app_with_the_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    started: dict[str, Any] = {}  # Any: the arguments uvicorn.run got.

    def fake_run(app: FastAPI, host: str, port: int, access_log: bool) -> None:
        started.update(app=app, host=host, port=port, access_log=access_log)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    with local_booking_server({"equip-sem": SEM}) as server:
        config = write_config(tmp_path, server.url)
        with config.open("a") as file:
            file.write("PUBLIC_BASE_URL=https://standup.example.org\nWEB_PORT=8081\n")
        code = main(["--config", str(config), "serve"])
        page = TestClient(started["app"]).get("/")

    assert code == 0
    assert (started["host"], started["port"]) == ("127.0.0.1", 8081)
    # Confirm links carry their token in the URL, so the access log would keep working tokens.
    assert started["access_log"] is False
    assert "TEST-SEM - Test Electron Microscope" in page.text


def test_serve_without_a_public_base_url_exits_with_code_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: pytest.fail("the web app must not start"))
    config = write_config(tmp_path, "http://127.0.0.1:1")

    assert main(["--config", str(config), "serve"]) == 2
