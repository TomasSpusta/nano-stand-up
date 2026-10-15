# Nano Stand-up

Weekly briefs by email for CF nano instrument guarantors.
Words follow [GLOSSARY.md](GLOSSARY.md).

## Set up

Needs [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

Copy `.env.example` to `.env` and fill it in.
`.env` holds the API key and is git-ignored: never commit it.

`SERVICE_ADMIN_EMAIL` is the service admin's address.
It gets an error report when briefs still fail at 10:00 Prague time.

`WORKING_DAY_START` and `WORKING_DAY_END` (default 07:00 and 19:00) set the working day, Monday to Friday.
The Heads-up section of the brief lists the reservations outside it.

`NEW_USER_RESERVATIONS` (default 3) sets how many first reservations on an instrument are by a new user.
They count from the start of the user's earliest certificate for the instrument.

## Test, lint, and format

```bash
uv run pytest
```

```bash
uv run mypy
```

```bash
uv run ruff check
```

Check that the code has the project format:

```bash
uv run ruff format --check
```

Format the code:

```bash
uv run ruff format
```

The formatter and the linter share the Ruff config in `pyproject.toml`, with a line length of 120.

The tests run offline and use made-up data only.

### Live API check

One opt-in test checks the real booking source against the live booking API, to catch API changes the fake would hide.
It only reads: it fails before sending any request other than a GET or the login.
It checks the instrument and its guarantor, a recent week's reservations with their experiment plans, the users' certificates and past reservations, and the session filters by instrument and by user that the API spec does not list.
Its messages hold only counts, field names, and ids, never names, emails, or the API key.

`uv run pytest` leaves it out.
It runs only with `-m live_api`, and it is skipped unless `BOOKING_API_BASE_URL`, `BOOKING_API_KEY`, and `LIVE_API_INSTRUMENT_ID` are set.
`LIVE_API_INSTRUMENT_ID` is the id of an instrument the API key can read that had reservations in the last 12 weeks.
Put it in `.env` or the environment, then run:

```bash
uv run --env-file .env pytest -m live_api -s
```

`-s` shows a one-line summary with counts.

## Run

Start Mailpit, a local mail catcher, so no real guarantor gets mail while testing:

```bash
docker run -d --name nano-standup-mailpit -p 127.0.0.1:8025:8025 -p 127.0.0.1:1025:1025 axllent/mailpit
```

Add a subscription by hand, without a confirm link (the email must be the instrument's guarantor email in the booking system):

```bash
uv run nano-standup add-subscription <instrument-id> <guarantor-email>
```

Send this week's briefs:

```bash
uv run nano-standup run-monday-job
```

Read them at http://localhost:8025.
The Monday job needs `PUBLIC_BASE_URL` too (see below), because each brief has an unsubscribe link that starts with it.

The server timer starts the Monday job once, at 08:00 Prague time.

Before each brief, the job checks that the subscriber's email is still the instrument's guarantor email in the booking system, ignoring case and surrounding spaces.
If not (the guarantor changed, or the guarantor's email changed), no brief goes out and the subscription ends.
The job then reads no reservations, certificates, or user history for that subscription.
The old address gets a short stop notice instead, once: an ended subscription is not checked again.
If the stop notice cannot be sent, the subscription still ends, and the stop notice is tried again like a brief (see below).
A stop notice to an address the mail server refuses for good is not tried again.
A stop notice still not sent at 10:00 is listed in the error report; a re-run does not send it.
If the database cannot end the subscription, no brief goes out, and it is tried again like a brief.
The log names each ended subscription by instrument and old address.
If the booking system has no guarantor email for an instrument, its brief fails each week and shows in the service admin error report, and the subscription is not ended.
The database keeps ended subscriptions.
A database file from an older version is upgraded the first time this version opens it, and it keeps its subscriptions.

When the booking API or the mail server fails for a brief or a stop notice, the job tries it again every 15 minutes, up to 10:00 Prague time.
The retries keep to the quarter hours of the Prague clock (08:15, 08:30, and so on), so the last one is at 10:00 even when the job started a few seconds late.
The 10:00 deadline is 10:00 Prague time on the day of the run, so a re-run on Tuesday at 08:00 retries until Tuesday 10:00.
One failing instrument does not stop the briefs for the others.
Other errors fail at once, with no retries: an instrument the booking system no longer has, an instrument the API key has no access to, booking data a brief cannot use, a recipient address the mail server refuses for good, or an unexpected error.
Briefs that still fail at 10:00 go to the service admin in one error report, which lists the failed instruments (by alias and id when known) and their errors.
The same report lists the stop notices that could not be sent.

Each sent brief is recorded per subscription and week.
So running the job again by hand sends only the briefs not sent yet that week.
A run started after 10:00 on its day tries each brief once, with no waiting.
Only one Monday job runs at a time: it holds a lock file next to the database (`<database>.monday-job.lock`).
A run started while another one runs sends nothing and exits with code 4.

The log shows each brief sent, each error, and a summary at the end: instruments processed, briefs sent, briefs failed, briefs sent but not recorded, and subscriptions ended because the guarantor changed.

Exit codes:

- `0`: all went well.
- `1`: an unexpected error; the log has the details.
- `2`: a setting, an instrument id, or a guarantor email is wrong.
- `3`: the Monday job ended with briefs not sent, briefs sent but not recorded (a re-run sends those again), or stop notices not sent; the service admin got the error report, unless the mail server was down too.
- `4`: another Monday job was still running, so this run sent nothing.

### Web app

Guarantors subscribe on a small web page.
They pick an instrument and enter their email.
When the email is the instrument's guarantor email in the booking system, a confirm link goes to that email.
The subscription starts when they open the link and press Confirm, if the email is still the guarantor email then.
Opening the link alone does not use it, because mail scanners open links too.
A subscription that ended because the guarantor changed starts again the same way.

Each brief ends with an unsubscribe link for its subscription.
Opening it shows an Unsubscribe button, and pressing it ends that subscription only.
The guarantor's other subscriptions keep going.
Each brief gets a new link, and every link of a subscription works until the subscription ends, with no time limit.
A brief sent again on a retry gets a new link too.
When the subscription ends, in either way, all its links stop working, so an old link never ends a later subscription to the same instrument.
An old link then says "Already unsubscribed"; after a guarantor change it also says that the booking system no longer lists the email as the guarantor, without saying who the guarantor is now.
Unsubscribing keeps the ended subscription in the database, as a guarantor change does.
The stop notice has no unsubscribe link, because its subscription has already ended.
The brief also carries the `List-Unsubscribe` and `List-Unsubscribe-Post` mail headers (RFC 8058), so mail apps can show their own unsubscribe button.

Set `PUBLIC_BASE_URL` in `.env` to the address the page is opened at, for example `http://localhost:8000` when testing.
Confirm and unsubscribe links start with it.
`WEB_HOST` and `WEB_PORT` (default 127.0.0.1 and 8000) set where the app listens.
In production it runs behind an HTTPS proxy.

```bash
uv run nano-standup serve
```

Open http://localhost:8000.
The page loads the instrument list from the booking API, which takes a few seconds, and keeps it for 30 minutes.
With Mailpit running, the confirm emails show at http://localhost:8025.
