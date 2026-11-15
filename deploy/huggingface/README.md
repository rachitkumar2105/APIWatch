---
title: APIWatch API
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
short_description: Task REST API with JWT, idempotency, optimistic locks
---

# APIWatch API

The backend of [APIWatch](https://github.com/rachitkumar2105/APIWatch): a FastAPI task-management
REST API with JWT authentication, idempotent creates (`Idempotency-Key`), optimistic locking
(`409` on stale writes), rate limits and Prometheus metrics.

- Interactive docs: `/docs` (opening the Space root redirects there)
- Liveness: `/health/live`, readiness: `/health/ready`

This Space is deployed from the GitHub repository by `scripts/deploy_hf_space.py`; edit the code
there, not here.

## Configuration (Settings > Variables and secrets)

| Name | Kind | Purpose |
|---|---|---|
| `JWT_SECRET_KEY` | Secret, required | Signs access tokens. 32+ characters. The API refuses to start without it. |
| `CORS_ORIGINS` | Variable | Comma-separated frontend origins, e.g. `https://apiwatch.vercel.app` |
| `CORS_ORIGIN_REGEX` | Variable, optional | Pattern for Vercel preview URLs |
| `DATABASE_URL` | Secret, optional | Postgres URL for data that survives restarts. Without it, SQLite inside the container is used and resets when the Space restarts. |
| `METRICS_TOKEN` | Secret, optional | Enables `/metrics` behind `Authorization: Bearer <token>` |
