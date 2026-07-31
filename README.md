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
- OpenAPI: <http://127.0.0.1:8000/api/openapi.json>
- Readiness: <http://127.0.0.1:8000/api/health/ready>

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
this server publicly.

The committed `openapi/openapi.json` file is the handoff contract for the PWA.
Regenerate it after intentional API changes.

All mutation requests require UUID values in `Idempotency-Key` and
`X-Device-ID`. Replaying same mutation returns stored result without applying it
twice. `GET /api/state` returns current aggregate plus monotonic revision used by
PWA synchronization.

Quantities cross API as decimal strings with exactly three fractional digits.
Canonical base units are grams, millilitres, and items.
