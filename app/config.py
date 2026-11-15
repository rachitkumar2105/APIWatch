"""Runtime configuration, read once from the environment at import time.

Everything deployment-specific lives here so the same image runs locally,
in Docker Compose, and on Hugging Face Spaces with only env vars changing.
"""

import os

from dotenv import load_dotenv

load_dotenv()

APP_VERSION = "1.2.0"

# "production" turns on fail-fast checks (a real JWT secret is required) and
# closes /metrics unless METRICS_TOKEN is set.
APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV == "production"


def split_csv(value: str) -> list[str]:
    """'a, b/,,c' -> ['a', 'b', 'c']. Drops trailing slashes (an Origin never has one)."""
    return [item.strip().rstrip("/") for item in value.split(",") if item.strip()]


# Exact origins allowed to call the API from a browser. Never a wildcard.
CORS_ORIGINS = split_csv(
    os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:4173,http://127.0.0.1:4173")
)
# Optional regex for origins that can't be listed up front, e.g. Vercel preview
# deployments: ^https://apiwatch-[a-z0-9-]+-rachitkumar2105s-projects\.vercel\.app$
CORS_ORIGIN_REGEX = os.getenv("CORS_ORIGIN_REGEX") or None

# Bearer token required to scrape /metrics. In production, /metrics is not
# served at all unless this is set.
METRICS_TOKEN = os.getenv("METRICS_TOKEN") or None

# Set to "true" only when running behind a reverse proxy you control or trust
# (Hugging Face Spaces, a load balancer). Then the rate limiter keys on the
# client IP from X-Forwarded-For instead of the proxy's own address.
TRUST_PROXY_HEADERS = os.getenv("TRUST_PROXY_HEADERS", "false").strip().lower() == "true"
