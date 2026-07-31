# Grocea Backend

Local-only Phase 0 API for Grocea. Backend owns Local Profile, global/custom
catalog, exact Pantry Stock, Recipe drafts and publishing, cooking, immutable
Activity Events, reversals, idempotent offline mutations, and legacy PWA import.

## Requirements

- [`uv`](https://docs.astral.sh/uv/)
- PostgreSQL 16+
- Local databases named `grocea` and `grocea_test`

## Setup

```bash
createdb grocea
createdb grocea_test
cp .env.example .env
uv sync
uv run grocea migrate
uv run grocea seed
```

Start the local API on loopback only:

```bash
uv run uvicorn grocea.main:app --reload --host 127.0.0.1
```

- API docs: <http://127.0.0.1:8000/api/docs>
- Developer landing: <http://127.0.0.1:8000/>
- API logs: <http://127.0.0.1:8000/logs>
- OpenAPI: <http://127.0.0.1:8000/api/openapi.json>
- Readiness: <http://127.0.0.1:8000/api/health/ready>

The developer log console polls a process-local ring buffer every two seconds.
It retains the latest 500 entries by default and clears whenever the process
restarts. Set `API_LOG_CAPACITY` to a value from 1 to 10,000 to tune the limit.
The console records product API request metadata and Grocea warnings and errors;
it never records query values, headers, cookies, request bodies, or response
bodies. Developer pages, documentation, the log feed, and health checks are
excluded from request history.

## Developer commands

```bash
uv run grocea migrate
uv run grocea seed
uv run grocea export-openapi
uv run grocea reset --yes

uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

`reset --yes` is destructive. It refuses non-loopback hosts and database names
other than `grocea` or `grocea_test`, recreates the `public` schema, then migrates
and seeds it.

Integration tests use `TEST_DATABASE_URL` and refuse databases not named
`grocea_test`.

## API boundary

All application routes live under `/api`. The API always resolves requests to
the stable seeded Local Profile; there is no Phase 0 authentication. Never bind
this server publicly. The unauthenticated developer landing, documentation, and
log console are subject to the same restriction.

The committed `openapi/openapi.json` file is the handoff contract for the PWA.
Regenerate it after intentional API changes.

All mutation requests require UUID values in `Idempotency-Key` and
`X-Device-ID`. Replaying same mutation returns stored result without applying it
twice. `GET /api/state` returns current aggregate plus monotonic revision used by
PWA synchronization.

Quantities cross API as decimal strings with exactly three fractional digits.
Canonical base units are grams, millilitres, and items.
