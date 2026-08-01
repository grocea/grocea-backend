# Grocea Authentication v1

Grocea v1 uses personal email/password accounts and opaque PostgreSQL-backed
sessions. Registration, login, session inspection, logout, and password
rotation live under `/api/auth`; product handlers continue to receive the
authenticated `User` through `CurrentUser`.

The browser receives a host-only `grocea_session` cookie (`HttpOnly`,
`SameSite=Lax`, `Path=/api`, 30-day lifetime). PostgreSQL stores only its
SHA-256 digest. Argon2id hashes passwords; the accepted password length is
15–128 characters without trimming or composition rules. Unsafe authenticated
requests require the session's `X-CSRF-Token` and an allowed `Origin`.

Fresh databases seed global catalog data only. Before exposing registration for
an existing installation, back up the database and run
`grocea claim-local-profile --email ...`; it prompts twice and preserves the
legacy user's stable UUID and owned records.
