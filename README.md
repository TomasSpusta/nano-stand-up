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

`WORKING_DAY_START` and `WORKING_DAY_END` (default 07:00 and 19:00) set the working day, Monday to Friday.
The Heads-up section of the brief lists the reservations outside it.

## Test and lint

```bash
uv run pytest
```

```bash
uv run mypy
```

```bash
uv run ruff check
```

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
