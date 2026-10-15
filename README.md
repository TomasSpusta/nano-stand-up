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

Add a subscription by hand, without a confirm link (the email must be the instrument's guarantor email in the booking system):

```bash
uv run nano-standup add-subscription <instrument-id> <guarantor-email>
```

Send this week's briefs:

```bash
uv run nano-standup run-monday-job
```

Read them at http://localhost:8025.

### Web app

Guarantors subscribe on a small web page.
They pick an instrument and enter their email.
When the email is the instrument's guarantor email in the booking system, a confirm link goes to that email.
The subscription starts when they open the link.

Set `PUBLIC_BASE_URL` in `.env` to the address the page is opened at, for example `http://localhost:8000` when testing.
Confirm links start with it.
`WEB_HOST` and `WEB_PORT` (default 127.0.0.1 and 8000) set where the app listens.
In production it runs behind an HTTPS proxy.

```bash
uv run nano-standup serve
```

Open http://localhost:8000.
The page loads the instrument list from the booking API, which takes a few seconds.
With Mailpit running, the confirm emails show at http://localhost:8025.
