# Grocea Backend

Grocea API with personal email/password accounts, opaque PostgreSQL-backed
sessions, global/custom catalog, exact Pantry Stock, Recipe drafts and
publishing, cooking, immutable Activity Events, reversals, idempotent offline
mutations, and explicit legacy PWA import.

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
uv run grocea claim-local-profile --email you@example.com
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

## Vercel + Supabase deployment

Vercel detects the FastAPI application through the `tool.vercel.entrypoint`
setting in `pyproject.toml`. Leave Vercel Build Command, Install Command, and
Output Directory unset; Vercel installs dependencies from `pyproject.toml` and
`uv.lock` and runs the exported ASGI application.

Set these Vercel environment variables for the production deployment:

```text
APP_ENV=production
DATABASE_URL=postgresql+psycopg://postgres.<project-ref>:<password>@aws-<region>.pooler.supabase.com:6543/postgres?sslmode=require
CORS_ORIGINS=https://<pwa-origin>
TRUSTED_HOSTS=<api-host>,<project>.vercel.app
AUTH_COOKIE_SAMESITE=lax
LOG_LEVEL=INFO
API_LOG_CAPACITY=500
```

Use Supabase's transaction pooler on port `6543` for Vercel requests. The
engine uses `NullPool` and disables psycopg prepared statements for this mode.
Use Supabase's direct connection on port `5432` only for migrations and seed
commands. Do not run migrations or seeding as a Vercel build command.

If the PWA and API are on unrelated domains such as `pages.dev` and
`vercel.app`, set `AUTH_COOKIE_SAMESITE=none` and use HTTPS. Prefer custom
domains under one parent domain, such as `app.example.com` and
`api.example.com`, and keep the default `lax` setting.

## API boundary

All application routes live under `/api`. Product routes require an authenticated
account session in the `grocea_session` HttpOnly cookie and an in-memory
`X-CSRF-Token` for unsafe requests. Sessions store only a SHA-256 token digest;
passwords use Argon2id. Cookies are host-only, `SameSite=Lax`, scoped to `/api`,
and Secure outside local/test environments. Auth responses are `no-store`.

`GET /api/health/live` and `GET /api/health/ready` remain public and minimal.
Developer landing, documentation, and log surfaces are local-only. Configure
exact credentialed `CORS_ORIGINS` and `TRUSTED_HOSTS` before deployment; keep the
API behind same-origin TLS and ingress throttling.

Fresh databases seed only global catalog data. Existing installations must be
backed up and claimed once with `grocea claim-local-profile --email ...` before
public registration is enabled; the command prompts twice and never accepts a
password argument. Claiming preserves the stable legacy user ID and all owned
foreign-key data.

The committed `openapi/openapi.json` file is the handoff contract for the PWA.
Regenerate it after intentional API changes.

All mutation requests require UUID values in `Idempotency-Key` and
`X-Device-ID`. Replaying same mutation returns stored result without applying it
twice. `GET /api/state` returns current aggregate plus monotonic revision used by
PWA synchronization.

Quantities cross API as decimal strings with exactly three fractional digits.
Canonical base units are grams, millilitres, and items.
