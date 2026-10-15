from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nano_standup.cli import main
from nano_standup.run_lock import run_lock
from nano_standup.subscriptions import Subscription, SubscriptionStore

from fake_booking_api import sem_json
from helpers import FakeSmtpServer
from local_booking_server import API_KEY, local_booking_server

SEM = sem_json()


def write_config(tmp_path: Path, booking_url: str, public_base_url: str | None = "https://standup.example.org") -> Path:
    path = tmp_path / ".env"
    path.write_text(
        f"BOOKING_API_BASE_URL={booking_url}\nBOOKING_API_KEY={API_KEY}\n"
        "SMTP_HOST=127.0.0.1\nSMTP_PORT=1\nMAIL_FROM=standup@example.org\nSERVICE_ADMIN_EMAIL=service-admin@example.org\n"
        "DATABASE_PATH=data/standup.sqlite3\n" + (f"PUBLIC_BASE_URL={public_base_url}\n" if public_base_url else "")
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


def test_briefs_still_not_sent_at_the_deadline_exit_with_code_3(tmp_path: Path) -> None:
    with local_booking_server({"equip-sem": SEM}) as server:
        config = write_config(tmp_path, server.url)
        main(["--config", str(config), "add-subscription", "equip-sem", "hana.testova@example.org"])
    # The server is stopped now, so the Monday job cannot reach the booking API.
    # The mail server at port 1 refuses too, so the error report to the service admin fails as well.
    now = datetime(2026, 10, 19, 6, 0, tzinfo=UTC)  # Monday 08:00 in Prague.
    waits: list[timedelta] = []

    def sleep(duration: timedelta) -> None:
        nonlocal now
        waits.append(duration)
        now += duration

    code = main(["--config", str(config), "run-monday-job"], clock=lambda: now, sleep=sleep)

    assert code == 3
    # Retries from 08:15 to 10:00.
    assert waits == [timedelta(minutes=15)] * 8


def test_a_stop_notice_not_sent_exits_with_code_3_and_still_ends_the_subscription(tmp_path: Path) -> None:
    now = datetime(2026, 10, 19, 6, 0, tzinfo=UTC)  # Monday 08:00 in Prague.
    database = tmp_path / "data/standup.sqlite3"
    database.parent.mkdir()
    # The booking system has another guarantor email for the instrument now.
    SubscriptionStore(database).add_active("equip-sem", "former.guarantor@example.org", now)
    waits: list[timedelta] = []

    def sleep(duration: timedelta) -> None:
        nonlocal now
        waits.append(duration)
        now += duration

    with local_booking_server({"equip-sem": SEM}) as server:
        config = write_config(tmp_path, server.url)
        # The mail server at port 1 refuses, so the stop notice cannot be sent.
        code = main(["--config", str(config), "run-monday-job"], clock=lambda: now, sleep=sleep)

    assert code == 3
    # The stop notice is tried again from 08:15 to 10:00, like a brief.
    assert waits == [timedelta(minutes=15)] * 8
    assert SubscriptionStore(database).list_active() == []


def test_an_unexpected_failure_exits_with_code_1(tmp_path: Path) -> None:
    config = write_config(tmp_path, "http://127.0.0.1:1")
    # A file where the database folder should be.
    (tmp_path / "data").write_text("")

    assert main(["--config", str(config), "run-monday-job"]) == 1


def test_the_monday_job_sends_briefs_with_unsubscribe_links_at_the_public_base_url(
    tmp_path: Path, smtp: FakeSmtpServer
) -> None:
    with local_booking_server({"equip-sem": SEM}) as server:
        config = write_config(tmp_path, server.url)
        main(["--config", str(config), "add-subscription", "equip-sem", "hana.testova@example.org"])
        code = main(
            ["--config", str(config), "run-monday-job"],
            clock=lambda: datetime(2026, 10, 19, 6, 0, tzinfo=UTC),  # Monday 08:00 in Prague.
            sleep=lambda _: pytest.fail("nothing to try again"),
        )

    assert code == 0
    [email] = smtp.sent
    plain = email.get_body(("plain",))
    assert plain is not None
    assert "Unsubscribe from this brief: https://standup.example.org/unsubscribe?token=" in plain.get_content()


def test_the_monday_job_without_a_public_base_url_exits_with_code_2(tmp_path: Path) -> None:
    config = write_config(tmp_path, "http://127.0.0.1:1", public_base_url=None)

    assert main(["--config", str(config), "run-monday-job"]) == 2


def test_serve_runs_the_web_app_with_the_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    started: dict[str, Any] = {}  # Any: the arguments uvicorn.run got.

    def fake_run(app: FastAPI, host: str, port: int, access_log: bool) -> None:
        started.update(app=app, host=host, port=port, access_log=access_log)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    with local_booking_server({"equip-sem": SEM}) as server:
        config = write_config(tmp_path, server.url)
        with config.open("a") as file:
            file.write("WEB_PORT=8081\n")
        code = main(["--config", str(config), "serve"])
        page = TestClient(started["app"]).get("/")

    assert code == 0
    assert (started["host"], started["port"]) == ("127.0.0.1", 8081)
    # Confirm links carry their token in the URL, so the access log would keep working tokens.
    assert started["access_log"] is False
    assert "TEST-SEM - Test Electron Microscope" in page.text


def test_serve_without_a_public_base_url_exits_with_code_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: pytest.fail("the web app must not start"))
    config = write_config(tmp_path, "http://127.0.0.1:1", public_base_url=None)

    assert main(["--config", str(config), "serve"]) == 2


def test_a_monday_job_started_while_one_runs_exits_with_code_4_and_sends_nothing(tmp_path: Path) -> None:
    config = write_config(tmp_path, "http://127.0.0.1:1")
    (tmp_path / "data").mkdir()
    SubscriptionStore(tmp_path / "data/standup.sqlite3").add_active(
        "equip-sem", "hana.testova@example.org", datetime(2026, 10, 19, 6, 0, tzinfo=UTC)
    )
    tries: list[datetime] = []

    def clock() -> datetime:
        tries.append(datetime(2026, 10, 19, 6, 0, tzinfo=UTC))
        return tries[-1]

    # The running job holds this lock.
    with run_lock(tmp_path / "data/standup.sqlite3.monday-job.lock"):
        code = main(["--config", str(config), "run-monday-job"], clock=clock, sleep=lambda _: None)

    assert code == 4
    # Not started at all: it never read the clock.
    assert tries == []
