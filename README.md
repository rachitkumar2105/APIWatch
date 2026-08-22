# APIWatch

A task-management REST API built to demonstrate: scalable API design, automated testing with a coverage gate, and production-style observability.

Deliberately kept deterministic and simple — no AI/LLM components — so every design decision is fully explainable under interview cross-questioning.

## What it does

FastAPI + SQLite REST API for managing tasks (create, read, update, delete, list with pagination and status filtering), backed by:

- **Automated testing** — pytest, one happy-path and one error-path test per endpoint, plus pagination edge cases, CORS, rate limiting, and the generic-error-handler behavior, run against an isolated in-memory database.
- **CI with coverage + lint gating** — GitHub Actions runs `ruff`, `black --check`, and the full test suite with `--cov-fail-under=85` on every push.
- **Observability** — `prometheus-fastapi-instrumentator` exposes request count, latency histograms, and error rate at `/metrics`; a Prometheus + Grafana stack (via Docker Compose) visualizes them.
- **Baseline security hygiene** — Pydantic validation on every request body, ORM-only DB access, an explicit CORS allow-list, a global exception handler that never leaks stack traces, and rate limiting on write endpoints.

## Architecture

```
                 ┌─────────────┐
  Client ──────► │   FastAPI   │ ──────► SQLAlchemy ──────► SQLite
                 │ (app/main)  │
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
├── main.py          # Routes, CORS, rate limiting, exception handling, instrumentator wiring
├── models.py         # SQLAlchemy Task model (indexed on status, created_at)
├── schemas.py         # Pydantic request/response schemas
├── crud.py             # DB operations
└── database.py          # Engine/session setup, loads .env
tests/
├── conftest.py           # In-memory SQLite fixture, TestClient override, limiter reset
└── test_tasks.py          # Happy-path + error-path tests, pagination, CORS, rate limit, 500 handler
scripts/
└── loadtest.py             # Hits GET /tasks N times, prints p50/p95/p99 latency
.github/workflows/ci.yml       # ruff + black + pytest/coverage gate on every push
docker-compose.yml                # api + Prometheus + Grafana
prometheus.yml                      # Scrape config
grafana/provisioning/                 # Auto-provisioned datasource + dashboard
.env.example                            # DATABASE_URL / CORS_ORIGINS / GRAFANA_ADMIN_PASSWORD placeholders
```

## Running locally

```bash
python -m venv .venv
.venv/Scripts/activate   # source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
cp .env.example .env     # optional — defaults work without it
uvicorn app.main:app --reload
```

- API: http://localhost:8000
- Interactive docs: http://localhost:8000/docs
- Metrics: http://localhost:8000/metrics

## API

| Method | Path                            | Description                                  |
|--------|----------------------------------|-----------------------------------------------|
| POST   | `/tasks`                        | Create a task (rate-limited, 30/min/IP)       |
| GET    | `/tasks?status=&limit=&offset=` | List tasks, paginated (limit ≤100, default 20)|
| GET    | `/tasks/{id}`                   | Get a single task                             |
| PUT    | `/tasks/{id}`                   | Update a task (rate-limited, 30/min/IP)       |
| DELETE | `/tasks/{id}`                   | Delete a task (rate-limited, 30/min/IP)       |

Task fields: `id`, `title`, `description`, `status` (`pending` / `in_progress` / `done`), `created_at`, `updated_at`.

`GET /tasks` returns `{"items": [...], "total": N, "limit": L, "offset": O}` rather than a bare array, so a client can build pagination controls without a second count query.

Errors: `404` for a missing task, `422` for invalid/missing input, `429` if a client exceeds the write rate limit, `500` (generic body, no internal detail) for anything unhandled.

**Idempotency**: `GET`, `PUT`, and `DELETE` are idempotent — repeating the same request leaves the resource in the same state, so a client can safely retry them after a timeout. `POST` is not — retrying a create blindly would create a duplicate task. This matters at scale because idempotent methods are the ones you can safely retry behind a load balancer or flaky network without extra deduplication logic.

## Design notes

- **Indexes**: `status` and `created_at` are indexed because `GET /tasks` filters on `status` and always sorts by `created_at` (newest first) — both are on the hot path for every list query.
- **Sync, not async**: routes are synchronous. SQLite + SQLAlchemy's sync engine is the simplest correct setup for a single-instance demo API with no genuinely concurrent I/O-bound work. Async would matter if this had to hold many slow concurrent connections (e.g. calling other slow services) — SQLite's local disk I/O doesn't benefit much from it, and would add real complexity (async SQLAlchemy + `aiosqlite`, or a swap to Postgres with `asyncpg`) for no measured gain here.
- **Connection pooling**: uses SQLAlchemy's default pool settings — not overridden, since nothing about this workload calls for tuning it.
- **CORS**: origins come from `CORS_ORIGINS` (comma-separated), default `http://localhost:3000`. Never a wildcard. A real deployment would restrict this to the actual frontend's origin.

## Tests and coverage

```bash
pytest --cov=app --cov-report=term-missing
```

Last measured run: **21 passed, 97.35% coverage.** The only uncovered lines are `get_db`'s `try/finally` in `app/database.py` — tests override that dependency entirely (standard FastAPI `TestClient` pattern), so the real generator body never executes under test.

Tests run against an in-memory SQLite database (via a `get_db` override in `tests/conftest.py`), reset per test, with no ordering dependency. The rate limiter is reset between tests so write-heavy tests (e.g. the pagination test that creates 25 tasks) don't trip the 429 threshold.

## CI

`.github/workflows/ci.yml` runs on every push and on PRs to `main`:

1. Install dependencies
2. `ruff check .`
3. `black --check .`
4. `pytest --cov=app --cov-fail-under=85`

Any step failing blocks the job. Verified locally by running the same three commands in sequence against the current code before each commit — actually pushing to GitHub and watching the Action execute is still something Rachit needs to do himself.

## Observability stack (Prometheus + Grafana)

```bash
docker compose up
```

- API: http://localhost:8000
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000 (login `admin` / `admin` by default — set `GRAFANA_ADMIN_PASSWORD` in `.env` to change it)

The dashboard (`grafana/provisioning/dashboards/apiwatch.json`) is auto-provisioned on startup and tracks requests/sec, error rate (5xx/sec), latency p50/p95, and requests by status code — built against the actual metric names `prometheus-fastapi-instrumentator` exposes (`http_requests_total`, `http_request_duration_seconds_bucket`, `http_request_duration_highr_seconds_bucket`), confirmed by curling a running `/metrics` endpoint.

**Docker Compose itself was not run end-to-end during this build** — Docker Desktop's daemon didn't come up in the build environment. What *was* verified: `docker compose config` parses both `docker-compose.yml` and `prometheus.yml` without error, and the Grafana dashboard JSON is valid JSON with queries matched to real, observed metric names. Before relying on this for a demo or an interview, run `docker compose up` yourself, open Grafana, and confirm the dashboard actually renders live data.

### Measured latency

```bash
python scripts/loadtest.py 127.0.0.1 8000 200
```

Real run against `GET /tasks` (200 requests, one persistent connection, local SQLite, single instance):

| Metric | Value |
|--------|-------|
| p50    | 6.33 ms |
| p95    | 9.94 ms |
| p99    | 18.52 ms |
| mean   | 6.92 ms |
| errors | 0 / 200 |

This is a *local, single-instance, unloaded* number — useful to show the endpoint works and to demonstrate the measurement methodology, not a claim about production throughput under concurrent load. (An earlier attempt using Python's `urllib` against `localhost` instead of `127.0.0.1`, opening a fresh connection per request, produced ~2-second latencies — that turned out to be a Windows DNS/IPv6-fallback artifact in that specific client code, not real API latency. Verified with `curl` before trusting either number; recorded here so the discrepancy has a paper trail instead of silently vanishing.)

## Known limitations

Written down explicitly rather than glossed over:

- **No authentication.** Any client that can reach the API can call any endpoint. Adding it would mean: issue a JWT on a `/login` endpoint (or accept one from an external identity provider), verify it in a FastAPI dependency on every route, and attach a `user_id` to tasks so `GET /tasks` scopes to the caller. Not built — don't imply otherwise.
- **No real load testing.** `scripts/loadtest.py` is a simple sequential-request script, not a concurrent load-testing tool. A proper load test (e.g. Locust, with many concurrent virtual users) would be the next step before quoting a throughput number.
- **Single instance only.** No horizontal scaling, no load balancer, no distributed rate-limit store (slowapi's default limiter is in-process — multiple instances would each enforce their own 30/minute independently, not a shared limit).
- **SQLite, not Postgres.** Fine for a demo; the ORM layer is structured so swapping `DATABASE_URL` to a Postgres connection string is the only change needed, but this was never actually tested against Postgres.
- **`docker compose up` was not verified end-to-end** in the build environment (see Observability section above) — verify it yourself before using the dashboard as proof of anything.

## Why this exists

This targets the testing and telemetry practices called out in Salesforce's public engineering job postings for SDE/AMTS roles — specifically "design automation frameworks," "write unit/functional tests," "drive up code coverage," and "use telemetry and metrics to drive operational excellence." Every piece here (the pytest suite, the coverage+lint gate, the Prometheus/Grafana stack) maps to one of those lines, and every number in this README was actually measured against running code, not estimated.

This project is inspired by researching Salesforce's publicly available engineering priorities. It does not use, and does not imply experience with, the Salesforce platform (Apex, LWC, Agentforce, Hyperforce).

## Author

Rachit Kumar Singh — final-year B.Tech CSE, KIIT University (graduating 2027)
