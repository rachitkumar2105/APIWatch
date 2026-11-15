# APIWatch

A task-management REST API built to demonstrate: scalable API design, real authentication, automated testing with a coverage gate, and production-style observability.

Deliberately kept deterministic and simple — no AI/LLM components — so every design decision is fully explainable under interview cross-questioning.

## Live demo

- **App:** https://apiwatch.vercel.app (frontend on Vercel)
- **API:** https://rachitkumar2105-apiwatch-api.hf.space (FastAPI on Hugging Face Spaces, interactive docs at `/docs`)

The app is a task board whose every click is a real API call, shown live in a request log beside it: the status code, the time it took, and what the server decided. "Start a guest session" gives you a private throwaway account. Two buttons replay the interesting cases on demand: the same create sent twice with one `Idempotency-Key` (one task, not two), and a save based on a stale version (`409`, nothing overwritten).

The backend runs on free hosting, so the first request after a quiet period can take up to a minute while the Space wakes up; the app shows "Waking the API" until it answers. Demo data can be reset at any time (see [Deployment](#deployment)).

## What it does

FastAPI + SQLite (Postgres-compatible, verified) REST API for managing tasks (create, read, update, delete, list with pagination and status filtering), backed by:

- **Authentication** — JWT-based, bcrypt password hashing, every task endpoint requires a valid token, tasks are scoped per-user so one account can never see or modify another's data.
- **Idempotency** — `POST /tasks` accepts an optional `Idempotency-Key` header; retrying the same request (e.g. after a client-side timeout) returns the original response instead of creating a duplicate.
- **Optimistic concurrency control** — every task carries a `version`; a `PUT` must supply the version it read, and a stale version is rejected with `409 Conflict` instead of silently overwriting a concurrent change.
- **Health checks** — `/health/live` (process up) and `/health/ready` (database actually reachable) for real deployment readiness/liveness probes.
- **Automated testing** — pytest, 51 tests covering happy paths, error paths, ownership isolation, idempotency, version conflicts, concurrent-request races, and health-check failure modes, run against an isolated in-memory database (the two race tests use a file-based database so threads can really interleave).
- **CI with coverage + lint gating** — GitHub Actions runs `ruff`, `black --check`, and the full test suite with `--cov-fail-under=85` on every push (currently at 98.34%).
- **Observability** — `prometheus-fastapi-instrumentator` exposes request count, latency histograms, and error rate at `/metrics`; a Prometheus + Grafana stack (via Docker Compose) visualizes them.
- **Baseline security hygiene** — Pydantic validation on every request body (length limits, trimmed titles, a strict username character set, bcrypt's 72-byte password limit enforced instead of silently truncated), ORM-only DB access, an explicit CORS allow-list, security headers on every response, a global exception handler that never leaks stack traces, rate limiting on write and auth endpoints keyed on the real client IP behind a proxy, a production mode that refuses to start without a strong JWT secret, and `pip-audit` in CI.
- **Frontend** — a dependency-free static app (HTML, CSS, one JS module) with guest/sign-in/register flows, a three-column board (drag and drop or move buttons), inline editing with conflict recovery, and the live request log. Strict Content-Security-Policy, no third-party scripts, fonts or cookies.

## Architecture

```
                 ┌─────────────┐
  Client ──────► │   FastAPI   │ ──► auth (JWT/bcrypt)
                 │ (app/main)  │ ──► SQLAlchemy ──► SQLite / Postgres
                 └──────┬──────┘
                        │ /metrics
                        ▼
                 ┌─────────────┐        ┌─────────┐
                 │ Prometheus  │ ─────► │ Grafana │
                 │ (scrapes    │        │(dashboard)
                 │  every 5s)  │        └─────────┘
                 └─────────────┘
```

## Project structure

```
app/
├── main.py          # Routes, auth wiring, CORS, security headers, rate limiting, health checks, /metrics
├── config.py         # Environment-driven settings (APP_ENV, CORS, metrics token, proxy trust)
├── auth.py           # Password hashing (bcrypt) + JWT issuance/verification (PyJWT)
├── models.py          # SQLAlchemy models: User, Task (versioned, owned), IdempotencyKey
├── schemas.py           # Pydantic request/response schemas
├── crud.py                # DB operations, all task queries scoped by owner_id
└── database.py              # Engine/session setup, loads .env
tests/
├── conftest.py               # In-memory SQLite fixture, auth_client fixture, limiter reset, race_env (file DB)
├── test_tasks.py               # CRUD, pagination, ownership isolation, rate limit, 500 handler
├── test_auth.py                  # Register/login, wrong-password vs no-such-user parity, token validation
├── test_idempotency.py             # Idempotency-Key dedup, per-user scoping
├── test_concurrency_version.py       # Optimistic-locking / 409 conflict behavior
├── test_health.py                      # Liveness/readiness, including DB-down simulation
├── test_idempotency_race.py              # Two concurrent POSTs, same Idempotency-Key -> one task
├── test_version_race.py                    # Two concurrent PUTs, same version -> one 200, one 409
└── test_deployment.py                       # Security headers, CORS, metrics token, proxy IPs, secret policy, input limits
frontend/
├── index.html, privacy.html, terms.html, 404.html
├── assets/app.js, assets/styles.css, assets/fonts/   # No framework, no runtime dependencies
├── scripts/build.mjs                       # Builds dist/ for Vercel, injecting APIWATCH_API_URL + CSP
├── scripts/serve.mjs                        # Local dev server (npm run dev, port 4173)
└── vercel.json                             # Build settings + security headers
scripts/
├── loadtest.py                           # Concurrent load test (ThreadPoolExecutor), prints p50/p95/p99
└── deploy_hf_space.py                      # Uploads the API to a Hugging Face Space and sets its secrets
deploy/huggingface/README.md                # Space card (sdk: docker, app_port: 7860)
.github/workflows/ci.yml                     # API lint/tests/audit, Docker boot check, frontend build
.github/workflows/deploy-backend.yml          # Optional auto-deploy of the API to Hugging Face
docker-compose.yml                             # api + Prometheus + Grafana
prometheus.yml                                   # Scrape config
grafana/provisioning/                              # Auto-provisioned datasource + dashboard
.env.example                                         # Every environment variable, documented
```

## Running locally

```bash
python -m venv .venv
.venv/Scripts/activate   # source .venv/bin/activate on macOS/Linux
pip install -r requirements-dev.txt   # runtime deps + pytest/ruff/black/pip-audit
cp .env.example .env     # set JWT_SECRET_KEY for anything beyond local dev
uvicorn app.main:app --reload
```

- API: http://localhost:8000
- Interactive docs: http://localhost:8000/docs
- Metrics: http://localhost:8000/metrics

Frontend (Node 18+, no `npm install` needed: there are no dependencies):

```bash
cd frontend
npm run dev     # http://localhost:4173, talks to the API on :8000
```

`requirements.txt` holds only what the production image needs; test and lint tools are in `requirements-dev.txt`.

## API

| Method | Path                              | Auth required | Description                                     |
|--------|-------------------------------------|:---:|--------------------------------------------------|
| POST   | `/auth/register`                   | – | Create an account (username + password)           |
| POST   | `/auth/login`                      | – | Exchange credentials for a JWT access token (`expires_in` in seconds) |
| GET    | `/auth/me`                         | ✅ | The account behind the token (lets a client check a stored token) |
| GET    | `/health/live`                     | – | Liveness probe — process is up                     |
| GET    | `/health/ready`                    | – | Readiness probe — database is reachable            |
| POST   | `/tasks`                           | ✅ | Create a task (rate-limited 30/min/IP; supports `Idempotency-Key` header) |
| GET    | `/tasks?status=&limit=&offset=`    | ✅ | List the caller's tasks, paginated (limit ≤100, default 20) |
| GET    | `/tasks/{id}`                      | ✅ | Get a single task (only if owned by the caller)    |
| PUT    | `/tasks/{id}`                      | ✅ | Update a task (rate-limited; requires current `version`, `409` on conflict) |
| DELETE | `/tasks/{id}`                      | ✅ | Delete a task (rate-limited)                       |
| GET    | `/metrics`                         | token | Prometheus metrics. Open in development; in production only with `METRICS_TOKEN` |
| GET    | `/`                                | – | Redirects to `/docs`                               |

Task fields: `id`, `title`, `description`, `status` (`pending` / `in_progress` / `done`), `created_at`, `updated_at`, `version`.

`GET /tasks` returns `{"items": [...], "total": N, "limit": L, "offset": O}` rather than a bare array, so a client can build pagination controls without a second count query.

Errors: `401` for missing/invalid credentials, `404` for a missing (or not-owned) task, `409` for a stale-version update or a duplicate username, `422` for invalid/missing input, `429` if a client exceeds a rate limit, `500` (generic body, no internal detail) for anything unhandled.

**Idempotency**: `GET`, `PUT`, and `DELETE` are naturally idempotent — repeating the same request leaves the resource in the same state. `POST /tasks` is not idempotent by default (a blind retry would create a duplicate task) — but now supports an optional client-supplied `Idempotency-Key` header: the server remembers the key (scoped per user) and returns the original response on a repeat, rather than creating a second task. Omitting the header preserves the old, non-idempotent behavior.

**Authentication**: register with `POST /auth/register`, then `POST /auth/login` (form-encoded `username`/`password`) to receive a bearer JWT. Send it as `Authorization: Bearer <token>` on every `/tasks` request. Tokens expire after 30 minutes by default (`ACCESS_TOKEN_EXPIRE_MINUTES`); there is deliberately no refresh-token flow yet — that's a scope decision (rotation/revocation add real complexity), not an oversight.

## Design notes

- **Indexes**: `status` and `created_at` are indexed on `tasks`; `owner_id` is indexed on both `tasks` and `idempotency_keys` since every query filters on it.
- **Optimistic locking, not pessimistic**: a `version` column checked atomically inside the `UPDATE ... WHERE version = ?` statement itself, rather than row-level locks, since write contention on a single task is expected to be rare — this trades a small chance of a client needing to retry for zero locking overhead on the common case.
- **Idempotency keys stored in the database, not in-memory**: survives a process restart, and scopes naturally per-user via the existing `owner_id` foreign key, and a unique constraint on `(owner_id, key)` makes the database the arbiter when two requests race with the same key — no separate cache/store needed at this scale.
- **Sync, not async**: routes are synchronous. SQLAlchemy's sync engine is the simplest correct setup for this workload; async would matter for many slow concurrent I/O-bound calls to *other* services, which this API doesn't make.
- **Passwords**: bcrypt (called directly; `passlib` is unmaintained and breaks on bcrypt 5), never a fast general-purpose hash — bcrypt's deliberate slowness and automatic per-password salt are exactly why it's the right tool here, not SHA-256. bcrypt only reads 72 bytes, so longer passwords are rejected with a clear error rather than silently truncated.
- **JWT**: signed with PyJWT (replacing `python-jose`, which had open CVEs), algorithm list pinned on decode. The secret comes from `JWT_SECRET_KEY`: in development a default only logs a warning; with `APP_ENV=production` (the Docker image default) the process refuses to start without a secret of at least 32 characters.
- **CORS**: origins come from `CORS_ORIGINS` (comma-separated), plus an optional `CORS_ORIGIN_REGEX` for preview deployments. Never a wildcard. Credentials mode is off because tokens travel in the `Authorization` header, not cookies.
- **Rate limits behind a proxy**: on Hugging Face every request arrives from the platform's proxy, so keying on the TCP peer would put all visitors in one bucket. With `TRUST_PROXY_HEADERS=true` the limiter walks `X-Forwarded-For` from the right and uses the first public address: the one the edge proxy appended. A client can prepend fake entries, but can't get past that one.
- **Security headers**: `nosniff`, `no-referrer`, HSTS, a `default-src 'none'` CSP on API responses (the docs pages keep their CDN assets), and `frame-ancestors` limited to Hugging Face, which embeds the Space page.

## Tests and coverage

```bash
pytest --cov=app --cov-report=term-missing
```

Last measured run: **82 passed, 98% coverage** (410 statements, 7 missed). It grew from an earlier 21-test/97.35% baseline, then 51 tests/98.34%, as auth, idempotency, optimistic locking, health checks, concurrency, and deployment-hardening tests were added. Uncovered: `get_db`'s `try/finally` in `app/database.py` (tests override that dependency entirely, the standard FastAPI `TestClient` pattern), and one defensive re-raise in `crud.create_task_with_idempotency_key` for an `IntegrityError` that is not a duplicate key.

Tests run against an in-memory SQLite database (via a `get_db` override in `tests/conftest.py`), reset per test, with no ordering dependency. An `auth_client` fixture pre-registers and logs in a user so most tests don't need to repeat the auth flow; `test_auth.py` tests that flow directly. The rate limiter is reset between tests so write-heavy tests don't trip the 429 threshold unintentionally.

## Concurrency fixes

Two check-then-act races were found in review and fixed. Each has a concurrent test that failed before the fix.

**Idempotency-Key.** Two simultaneous `POST /tasks` with the same key could both look up the key, find nothing, and both create a task. Fix: a unique constraint on `idempotency_keys (owner_id, key)`, and the task and its key are now written in one transaction (`crud.create_task_with_idempotency_key`). The losing request gets an `IntegrityError`, rolls back (which also discards its task), and returns the winner's stored response. A unique constraint alone would not have been enough, because the old code committed the task before it saved the key.

**Optimistic lock.** Two simultaneous `PUT`s with the same `version` could both pass a Python-side version check, and the second silently overwrote the first. Fix: the check is part of the SQL, `UPDATE ... SET version = version + 1 WHERE id=? AND owner_id=? AND version=?`, and `rowcount == 0` means the version was stale, which returns `409`. A missing or not-owned task still returns `404`.

**How the tests reproduce the races.** They run on a file-based SQLite database (the shared in-memory connection used by the other tests cannot interleave), use a `threading.Barrier` so both requests start together, and wrap one lookup in a short `time.sleep` so the race window is reliably open instead of a few microseconds. Before the fixes they failed with two different task ids and with `[200, 200]` instead of `[200, 409]`. They pass 5 runs in a row on SQLite, and 3 runs in a row against a disposable PostgreSQL 16 container (`RACE_TEST_DATABASE_URL`). The rest of the suite uses in-memory SQLite and was not run on Postgres.

**Upgrading an existing database:** `create_all()` does not alter existing tables, so a database created before this change needs the unique constraint added (or the table recreated).

## CI

`.github/workflows/ci.yml` runs on every push and on PRs to `main`, as three jobs:

1. **API**: `ruff check .`, `black --check .`, `pytest --cov=app --cov-fail-under=85`, then `pip-audit -r requirements.txt` (fails on any known vulnerability in a runtime dependency).
2. **Production image boots**: builds the Dockerfile, runs it the way Hugging Face does (port 7860, production mode, a generated secret) and waits for `/health/ready`.
3. **Frontend build**: syntax-checks the JS and runs the Vercel build with a sample API URL.

Any step failing blocks the job.

## Observability stack (Prometheus + Grafana)

```bash
docker compose up
```

- API: http://localhost:8000
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000 (login `admin` / `admin` by default — set `GRAFANA_ADMIN_PASSWORD` in `.env` to change it)

The dashboard (`grafana/provisioning/dashboards/apiwatch.json`) is auto-provisioned on startup and tracks requests/sec, error rate (5xx/sec), latency p50/p95, and requests by status code — built against the actual metric names `prometheus-fastapi-instrumentator` exposes, confirmed by curling a running `/metrics` endpoint.

**Verified end-to-end by Rachit:** `docker compose up` brings up all three containers (api, prometheus, grafana) cleanly; the dashboard auto-provisions, loads live data while traffic runs, and correctly falls back to "No data" once the stack is stopped — confirming the readings are live, not cached.

### Measured latency

```bash
python scripts/loadtest.py 127.0.0.1 8000 200 1     # sequential baseline
python scripts/loadtest.py 127.0.0.1 8000 200 20    # concurrent, 20 workers
```

Real runs against `GET /tasks`, authenticated, local single instance:

| Concurrency | p50 | p95 | p99 | mean | errors |
|---|---|---|---|---|---|
| 1 (sequential) | 20.14 ms | 33.95 ms | 43.87 ms | 18.44 ms | 0 / 100 |
| 20 (concurrent) | 7.73 ms | 46.54 ms | 67.43 ms | 14.41 ms | 0 / 200 |

These numbers are **hardware- and OS-dependent, not universal constants** — an earlier run of this same script on different hardware measured p50 254ms/p95 415ms at 20-way concurrency, over 30x higher than the numbers above. Both are real, measured results; neither is "the" correct number for this API. The script itself opens a fresh HTTP connection per request (no connection pooling in the client), so on Windows especially, per-connection setup overhead is a real component of what's being measured here, not pure server-side latency — worth knowing before quoting either number as a throughput ceiling. The honest takeaway is qualitative, not the exact digits: this single-instance, synchronous, SQLite-backed design behaves reasonably under modest concurrent load, and the real bottleneck at scale would be the single-instance rate limiter and connection handling, not raw request latency.

## Database portability (SQLite ↔ Postgres)

The `DATABASE_URL` swap is not just a theoretical claim — it was run end-to-end against a real Postgres 16 instance (a disposable Docker container, not a shared/persistent one):

```bash
pip install psycopg2-binary   # not needed for SQLite; required only for Postgres
export DATABASE_URL="postgresql://postgres:<password>@localhost:5432/apiwatch"
uvicorn app.main:app
```

Note the driver: SQLAlchemy needs `psycopg2` (or another Postgres DBAPI driver) to talk to Postgres at all — it's now pinned in `requirements.txt`, but it's worth calling out explicitly since "just change `DATABASE_URL`" undersells it slightly without this.

Verified with no application code changes beyond the environment variable and that one added dependency: table creation (`Base.metadata.create_all`), user registration + JWT login, task creation, filtered listing, idempotent `POST` retries, an optimistic-locking `200` → `409` sequence, cross-user ownership isolation (404, not a leak), and deletion — then confirmed directly via `psql` against the live Postgres database (not just the app's own responses) that the `users`, `tasks`, and `idempotency_keys` tables existed with the expected rows.

## Known limitations

Written down explicitly rather than glossed over:

- **Single-instance rate limiting.** `slowapi`'s limiter is in-memory, per-process. Multiple instances behind a load balancer would each enforce their own separate 30/minute limit instead of one shared limit — fixing that means moving the counter to a shared store (Redis) with atomic increment-and-expire per client IP. Not built; the fix is well understood, just not implemented.
- **Demo hosting is free-tier.** Without a `DATABASE_URL`, the live API keeps its data in SQLite inside the Space, which is wiped when the Space restarts or rebuilds; point `DATABASE_URL` at a managed Postgres (e.g. Neon) for persistence. The Space also sleeps after a period without traffic and takes up to a minute to wake. The latency figures above were measured locally, not against the hosted demo.
- **No schema migrations.** Tables are created with `create_all()`. Fine for this schema on a fresh database; a real service would use Alembic.

## Deployment

Backend on Hugging Face Spaces (Docker), frontend on Vercel. Full step-by-step guide, including the environment variables for each side: [DEPLOYMENT.md](DEPLOYMENT.md).

## Why this exists

This targets the testing and telemetry practices called out in Salesforce's public engineering job postings for SDE/AMTS roles — specifically "design automation frameworks," "write unit/functional tests," "drive up code coverage," and "use telemetry and metrics to drive operational excellence." Every piece here (auth, idempotency, optimistic locking, the pytest suite, the coverage+lint gate, the Prometheus/Grafana stack) maps to a real engineering concern, and every number in this README was actually measured against running code, not estimated.

This project is inspired by researching Salesforce's publicly available engineering priorities. It does not use, and does not imply experience with, the Salesforce platform (Apex, LWC, Agentforce, Hyperforce).

## Author

Rachit Kumar Singh — final-year B.Tech CSE, KIIT University (graduating 2027)
