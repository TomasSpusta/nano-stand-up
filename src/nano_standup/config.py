"""Settings, read once at start from a git-ignored .env file. Environment variables win over the file."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path

from nano_standup.domain import WorkingHours
from nano_standup.mail import SmtpSettings

# Used when the settings leave out WORKING_DAY_START or WORKING_DAY_END.
DEFAULT_WORKING_DAY_START = "07:00"
DEFAULT_WORKING_DAY_END = "19:00"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    booking_api_base_url: str
    booking_api_key: str
    smtp: SmtpSettings
    mail_from: str
    database_path: Path
    # The Heads-up box points out reservations outside these hours.
    working_hours: WorkingHours


def load_settings(path: Path, environ: Mapping[str, str]) -> Settings:
    """Read settings. A relative DATABASE_PATH is taken from the config file's folder."""
    values = {**_read_env_file(path), **environ}

    def required(key: str) -> str:
        value = values.get(key, "").strip()
        if not value:
            raise ConfigError(f"Missing setting {key} in {path} or the environment")
        return value

    def optional(key: str) -> str | None:
        return values.get(key, "").strip() or None

    port_text = required("SMTP_PORT")
    try:
        port = int(port_text)
    except ValueError:
        raise ConfigError(f"Setting SMTP_PORT must be a number, got {port_text!r}") from None

    def clock_time(key: str, default: str) -> time:
        text = optional(key) or default
        try:
            return datetime.strptime(text, "%H:%M").time()
        except ValueError:
            raise ConfigError(f"Setting {key} must be a time such as {default}, got {text!r}") from None

    # Read before the try: ConfigError is a ValueError too.
    working_day_start = clock_time("WORKING_DAY_START", DEFAULT_WORKING_DAY_START)
    working_day_end = clock_time("WORKING_DAY_END", DEFAULT_WORKING_DAY_END)
    try:
        working_hours = WorkingHours(working_day_start, working_day_end)
    except ValueError as error:
        raise ConfigError(f"Settings WORKING_DAY_START and WORKING_DAY_END: {error}") from None

    starttls_text = (optional("SMTP_STARTTLS") or "false").lower()
    if starttls_text not in ("true", "false"):
        raise ConfigError(f"Setting SMTP_STARTTLS must be true or false, got {starttls_text!r}")

    return Settings(
        booking_api_base_url=required("BOOKING_API_BASE_URL"),
        booking_api_key=required("BOOKING_API_KEY"),
        smtp=SmtpSettings(
            host=required("SMTP_HOST"),
            port=port,
            username=optional("SMTP_USERNAME"),
            password=optional("SMTP_PASSWORD"),
            starttls=starttls_text == "true",
        ),
        mail_from=required("MAIL_FROM"),
        database_path=path.parent / required("DATABASE_PATH"),
        working_hours=working_hours,
    )


def _read_env_file(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ConfigError(f"Cannot read config file {path}: {error.strerror}") from error
    values = {}
    for number, line in enumerate(lines, start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            raise ConfigError(f"Cannot read config file {path}: line {number} has no '='")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values
