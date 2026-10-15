"""Command line: wires the real modules together from the settings."""

import argparse
import logging
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from nano_standup.booking_api import ApiBookingSource, HttpBookingApi
from nano_standup.booking_source import InstrumentNotFound
from nano_standup.config import ConfigError, Settings, load_settings
from nano_standup.domain import Clock
from nano_standup.mail import SmtpMailSender
from nano_standup.monday_job import Sleep, run_monday_job
from nano_standup.run_lock import AlreadyRunning, run_lock
from nano_standup.sent_briefs import SentBriefStore
from nano_standup.service_admin import NotTheGuarantor, add_subscription
from nano_standup.subscriptions import SubscriptionStore

log = logging.getLogger("nano_standup")

# Exit codes. 0 means all went well.
EXIT_UNEXPECTED_ERROR = 1
# A setting, an instrument id, or a guarantor email is wrong.
EXIT_INPUT_ERROR = 2
# The Monday job ended with briefs not sent, briefs sent but not recorded, or stop notices not sent.
# The service admin got an error report, if the mail server took it.
EXIT_BRIEFS_NOT_SENT = 3
# Another Monday job is still running, so this one sent nothing.
EXIT_ALREADY_RUNNING = 4


def system_clock() -> datetime:
    return datetime.now(UTC)


def system_sleep(duration: timedelta) -> None:
    time.sleep(duration.total_seconds())


def main(argv: list[str] | None = None, clock: Clock = system_clock, sleep: Sleep = system_sleep) -> int:
    """`clock` and `sleep` are here for tests: the Monday job waits between retries."""
    parser = argparse.ArgumentParser(
        prog="nano-standup", description="Weekly briefs for CF nano instrument guarantors."
    )
    parser.add_argument("--config", type=Path, default=Path(".env"), help="settings file (default: .env)")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add-subscription", help="add an active subscription without a confirm link")
    add.add_argument("instrument_id", help="the instrument id in the booking system")
    add.add_argument("email", help="the guarantor email the booking system has for the instrument")
    commands.add_parser("run-monday-job", help="send this week's brief for every active subscription")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        settings = load_settings(args.config, os.environ)
        if args.command == "add-subscription":
            return _add_subscription(settings, args.instrument_id, args.email, clock)
        return _run_monday_job(settings, clock, sleep)
    except (ConfigError, InstrumentNotFound, NotTheGuarantor) as error:
        log.error("%s", error)
        return EXIT_INPUT_ERROR
    except AlreadyRunning as error:
        log.error("%s. This run sends nothing.", error)
        return EXIT_ALREADY_RUNNING
    except Exception:
        log.exception("%s failed", args.command)
        return EXIT_UNEXPECTED_ERROR


def _add_subscription(settings: Settings, instrument_id: str, email: str, clock: Clock) -> int:
    subscription = add_subscription(_booking_source(settings), _store(settings), instrument_id, email, clock)
    log.info("Active subscription: %s gets the brief for %s", subscription.email, subscription.instrument_id)
    return 0


def _run_monday_job(settings: Settings, clock: Clock, sleep: Sleep) -> int:
    subscriptions = _store(settings)
    database = settings.database_path
    with run_lock(database.with_name(f"{database.name}.monday-job.lock")):
        result = run_monday_job(
            _booking_source(settings),
            subscriptions,
            SentBriefStore(database),
            SmtpMailSender(settings.smtp),
            clock=clock,
            sleep=sleep,
            from_address=settings.mail_from,
            service_admin_email=settings.service_admin_email,
            working_hours=settings.working_hours,
        )
    return EXIT_BRIEFS_NOT_SENT if result.failed or result.not_recorded or result.stop_notices_not_sent else 0


def _booking_source(settings: Settings) -> ApiBookingSource:
    return ApiBookingSource(HttpBookingApi(settings.booking_api_base_url, settings.booking_api_key))


def _store(settings: Settings) -> SubscriptionStore:
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    return SubscriptionStore(settings.database_path)


if __name__ == "__main__":
    sys.exit(main())
