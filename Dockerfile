# Production image. The same file runs on Hugging Face Spaces (Docker SDK,
# port 7860) and in the local docker-compose stack (which overrides PORT/APP_ENV).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Hugging Face Spaces runs containers as uid 1000. Matching it means the app
# can write its SQLite data directory without running as root.
RUN useradd --create-home --uid 1000 user
WORKDIR /home/user/app

COPY --chown=user:user requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=user:user app ./app
RUN mkdir -p data && chown user:user data

USER user

# Production defaults. Secrets (JWT_SECRET_KEY, optional DATABASE_URL,
# METRICS_TOKEN) are injected at runtime, never baked into the image.
ENV APP_ENV=production \
    TRUST_PROXY_HEADERS=true \
    PORT=7860 \
    DATABASE_URL=sqlite:////home/user/app/data/apiwatch.db

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health/live', timeout=4)"

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --no-server-header"]
