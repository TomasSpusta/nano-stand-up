"""Command line: wires the real modules together from the settings."""

import argparse
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import uvicorn

from nano_standup.booking_api import ApiBookingSource, HttpBookingApi
from nano_standup.booking_source import InstrumentNotFound
from nano_standup.config import ConfigError, Settings, load_settings
from nano_standup.domain import NotTheGuarantor
from nano_standup.mail import SmtpMailSender
from nano_standup.monday_job import run_monday_job
from nano_standup.service_admin import add_subscription
from nano_standup.subscription_request import SubscriptionRequests
from nano_standup.subscriptions import SubscriptionStore
from nano_standup.web import create_app

log = logging.getLogger("nano_standup")


def system_clock() -> datetime:
    return datetime.now(UTC)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nano-standup", description="Weekly briefs for CF nano instrument guarantors."
    )
    parser.add_argument("--config", type=Path, default=Path(".env"), help="settings file (default: .env)")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add-subscription", help="add an active subscription without a confirm link")
    add.add_argument("instrument_id", help="the instrument id in the booking system")
    add.add_argument("email", help="the guarantor email the booking system has for the instrument")
    commands.add_parser("run-monday-job", help="send this week's brief for every active subscription")
    commands.add_parser("serve", help="run the web app where guarantors subscribe")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        settings = load_settings(args.config, os.environ)
        if args.command == "add-subscription":
            return _add_subscription(settings, args.instrument_id, args.email)
        if args.command == "serve":
            return _serve(settings)
        return _run_monday_job(settings)
    except (ConfigError, InstrumentNotFound, NotTheGuarantor) as error:
        log.error("%s", error)
        return 2
    except Exception:
        log.exception("%s failed", args.command)
        return 1


def _add_subscription(settings: Settings, instrument_id: str, email: str) -> int:
    subscription = add_subscription(_booking_source(settings), _store(settings), instrument_id, email, system_clock)
    log.info("Active subscription: %s gets the brief for %s", subscription.email, subscription.instrument_id)
    return 0


def _run_monday_job(settings: Settings) -> int:
    sent = run_monday_job(
        _booking_source(settings),
        _store(settings),
        SmtpMailSender(settings.smtp),
        clock=system_clock,
        from_address=settings.mail_from,
        working_hours=settings.working_hours,
    )
    log.info("Monday job done: %d briefs sent", sent)
    return 0


def _serve(settings: Settings) -> int:
    if settings.public_base_url is None:
        raise ConfigError("Missing setting PUBLIC_BASE_URL, which the web app needs for its confirm links")
    requests = SubscriptionRequests(
        source=_booking_source(settings),
        store=_store(settings),
        mail_sender=SmtpMailSender(settings.smtp),
        clock=system_clock,
        from_address=settings.mail_from,
        public_base_url=settings.public_base_url,
    )
    app = create_app(requests)
    # No access log: it writes each full URL, and a confirm link's URL holds its token. The app logs what matters.
    uvicorn.run(app, host=settings.web_host, port=settings.web_port, access_log=False)
    return 0


def _booking_source(settings: Settings) -> ApiBookingSource:
    return ApiBookingSource(HttpBookingApi(settings.booking_api_base_url, settings.booking_api_key))


def _store(settings: Settings) -> SubscriptionStore:
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    return SubscriptionStore(settings.database_path)


if __name__ == "__main__":
    sys.exit(main())
