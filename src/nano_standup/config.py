"""Settings, read once at start from a git-ignored .env file. Environment variables win over the file."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path

from nano_standup.domain import WorkingHours
from nano_standup.heads_ups import HeadsUpSettings
from nano_standup.mail import SmtpSettings

# Used when the settings leave out WORKING_DAY_START, WORKING_DAY_END, or NEW_USER_RESERVATIONS.
DEFAULT_WORKING_DAY_START = "07:00"
DEFAULT_WORKING_DAY_END = "19:00"
DEFAULT_NEW_USER_RESERVATIONS = "3"

# Used when the settings leave out WEB_HOST or WEB_PORT. Only this server can reach the web app; a proxy in front
# of it gives HTTPS.
DEFAULT_WEB_HOST = "127.0.0.1"
DEFAULT_WEB_PORT = "8000"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    booking_api_base_url: str
    booking_api_key: str
    smtp: SmtpSettings
    mail_from: str
    # Gets the error report when briefs still fail at the retry deadline.
    service_admin_email: str
    database_path: Path
    # The limits of the heads-up rules, such as the working hours.
    heads_up: HeadsUpSettings
    # The address guarantors open the web app at, without a trailing slash. Confirm links and the unsubscribe links
    # in the briefs start with it. Only the web app and the Monday job need it, so it may be missing for the others.
    public_base_url: str | None
    # Where the web app listens.
    web_host: str
    web_port: int


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

    def number(key: str, text: str) -> int:
        try:
            return int(text)
        except ValueError:
            raise ConfigError(f"Setting {key} must be a number, got {text!r}") from None

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

    new_user_text = optional("NEW_USER_RESERVATIONS") or DEFAULT_NEW_USER_RESERVATIONS
    # isascii: isdigit alone takes digits such as "²", which int() refuses.
    if not (new_user_text.isascii() and new_user_text.isdigit()) or int(new_user_text) < 1:
        raise ConfigError(f"Setting NEW_USER_RESERVATIONS must be a whole number of 1 or more, got {new_user_text!r}")

    starttls_text = (optional("SMTP_STARTTLS") or "false").lower()
    if starttls_text not in ("true", "false"):
        raise ConfigError(f"Setting SMTP_STARTTLS must be true or false, got {starttls_text!r}")

    public_base_url = optional("PUBLIC_BASE_URL")
    if public_base_url is not None and not public_base_url.startswith(("https://", "http://")):
        raise ConfigError(f"Setting PUBLIC_BASE_URL must start with https:// or http://, got {public_base_url!r}")

    return Settings(
        booking_api_base_url=required("BOOKING_API_BASE_URL"),
        booking_api_key=required("BOOKING_API_KEY"),
        smtp=SmtpSettings(
            host=required("SMTP_HOST"),
            port=number("SMTP_PORT", required("SMTP_PORT")),
            username=optional("SMTP_USERNAME"),
            password=optional("SMTP_PASSWORD"),
            starttls=starttls_text == "true",
        ),
        mail_from=required("MAIL_FROM"),
        service_admin_email=required("SERVICE_ADMIN_EMAIL"),
        database_path=path.parent / required("DATABASE_PATH"),
        heads_up=HeadsUpSettings(working_hours=working_hours, new_user_reservations=int(new_user_text)),
        public_base_url=public_base_url.rstrip("/") if public_base_url else None,
        web_host=optional("WEB_HOST") or DEFAULT_WEB_HOST,
        web_port=number("WEB_PORT", optional("WEB_PORT") or DEFAULT_WEB_PORT),
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
