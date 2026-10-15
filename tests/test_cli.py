from pathlib import Path

from nano_standup.cli import main
from nano_standup.subscriptions import Subscription, SubscriptionStore

from fake_booking_api import sem_json
from local_booking_server import API_KEY, local_booking_server

SEM = sem_json()


def write_config(tmp_path: Path, booking_url: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(
        f"BOOKING_API_BASE_URL={booking_url}\nBOOKING_API_KEY={API_KEY}\n"
        "SMTP_HOST=127.0.0.1\nSMTP_PORT=1\nMAIL_FROM=standup@example.org\nSERVICE_ADMIN_EMAIL=admin@example.org\nDATABASE_PATH=data/standup.sqlite3\n"
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
