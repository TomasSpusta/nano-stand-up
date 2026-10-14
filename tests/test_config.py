from pathlib import Path

import pytest

from nano_standup.config import ConfigError, load_settings
from nano_standup.mail import SmtpSettings

FULL = """
# Comments and blank lines are fine.
BOOKING_API_BASE_URL=https://booking.example.org/api
BOOKING_API_KEY="test-key"
SMTP_HOST=localhost
SMTP_PORT=1025
MAIL_FROM=standup@example.org
DATABASE_PATH=data/standup.sqlite3
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(text)
    return path


def test_reads_all_settings_from_the_file(tmp_path: Path) -> None:
    settings = load_settings(write(tmp_path, FULL), environ={})

    assert settings.booking_api_base_url == "https://booking.example.org/api"
    assert settings.booking_api_key == "test-key"
    assert settings.smtp == SmtpSettings(host="localhost", port=1025, username=None, password=None, starttls=False)
    assert settings.mail_from == "standup@example.org"
    assert settings.database_path == tmp_path / "data/standup.sqlite3"


def test_optional_smtp_login_and_starttls(tmp_path: Path) -> None:
    text = FULL + "SMTP_USERNAME=standup\nSMTP_PASSWORD=test-password\nSMTP_STARTTLS=true\n"

    smtp = load_settings(write(tmp_path, text), environ={}).smtp

    assert smtp == SmtpSettings(host="localhost", port=1025, username="standup", password="test-password", starttls=True)


def test_environment_wins_over_the_file(tmp_path: Path) -> None:
    settings = load_settings(write(tmp_path, FULL), environ={"SMTP_HOST": "mail.example.org"})

    assert settings.smtp.host == "mail.example.org"


def test_a_missing_setting_is_named(tmp_path: Path) -> None:
    text = FULL.replace("BOOKING_API_KEY=\"test-key\"\n", "")

    with pytest.raises(ConfigError, match="BOOKING_API_KEY"):
        load_settings(write(tmp_path, text), environ={})


def test_a_bad_port_is_named(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="SMTP_PORT"):
        load_settings(write(tmp_path, FULL.replace("1025", "abc")), environ={})


def test_a_missing_file_is_named(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="nope.env"):
        load_settings(tmp_path / "nope.env", environ={})


def test_a_bad_starttls_value_is_named(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="SMTP_STARTTLS must be true or false, got 'yes'"):
        load_settings(write(tmp_path, FULL + "SMTP_STARTTLS=yes\n"), environ={})


def test_a_line_without_an_equals_sign_is_named(tmp_path: Path) -> None:
    path = write(tmp_path, "# comment\nSMTP_HOST localhost\n")

    with pytest.raises(ConfigError, match=r"\.env: line 2 has no '='"):
        load_settings(path, environ={})
