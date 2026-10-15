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

## Run

Start Mailpit, a local mail catcher, so no real guarantor gets mail while testing:

```bash
docker run -d --name nano-standup-mailpit -p 127.0.0.1:8025:8025 -p 127.0.0.1:1025:1025 axllent/mailpit
```

Add a subscription (the email must be the instrument's guarantor email in the booking system):

```bash
uv run nano-standup add-subscription <instrument-id> <guarantor-email>
```

Send this week's briefs:

```bash
uv run nano-standup run-monday-job
```

Read them at http://localhost:8025.

The server timer starts the Monday job once, at 08:00 Prague time.

Before each brief, the job checks that the subscriber's email is still the instrument's guarantor email in the booking system, ignoring case and surrounding spaces.
If not (the guarantor changed, or the guarantor's email changed), no brief goes out and the subscription ends.
The old address gets a short stop notice instead, once: an ended subscription is not checked again.
If the stop notice cannot be sent, the subscription still ends, and the stop notice is tried again like a brief (see below).
A stop notice still not sent at 10:00 is listed in the error report; a re-run does not send it.
If the database cannot end the subscription, no brief goes out, and it is tried again like a brief.
The log names each ended subscription by instrument and old address.
If the booking system has no guarantor email for an instrument, its brief fails each week and shows in the service admin error report, and the subscription is not ended.
The database keeps ended subscriptions.
A database file from an older version is upgraded the first time this version opens it, and it keeps its subscriptions.

When the booking API or the mail server fails for a brief or a stop notice, the job tries it again every 15 minutes, up to 10:00 Prague time.
One failing instrument does not stop the briefs for the others.
Other errors fail at once, with no retries: an instrument the booking system no longer has, booking data a brief cannot use, or an unexpected error.
Briefs that still fail at 10:00 go to the service admin in one error report, which lists the failed instruments (by alias and id when known) and their errors.
The same report lists the stop notices that could not be sent.

Each sent brief is recorded per subscription and week.
So running the job again by hand sends only the briefs not sent yet that week.
A run started after 10:00 tries each brief once, with no waiting.
Only one Monday job runs at a time: it holds a lock file next to the database (`<database>.monday-job.lock`).
A run started while another one runs sends nothing and exits with code 4.

The log shows each brief sent, each error, and a summary at the end: instruments processed, briefs sent, briefs failed, briefs sent but not recorded, and subscriptions ended because the guarantor changed.

Exit codes:

- `0`: all went well.
- `1`: an unexpected error; the log has the details.
- `2`: a setting, an instrument id, or a guarantor email is wrong.
- `3`: the Monday job ended with briefs not sent, briefs sent but not recorded (a re-run sends those again), or stop notices not sent; the service admin got the error report, unless the mail server was down too.
- `4`: another Monday job was still running, so this run sent nothing.
