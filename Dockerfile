# syntax=docker/dockerfile:1
# ---- Builder: compile all deps to wheels on a slim base ----
FROM python:3.11-slim AS builder
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*
# requirements first -> this layer caches until requirements.txt changes.
COPY requirements.txt ./
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# ---- Runtime: slim, non-root, no build toolchain ----
FROM python:3.11-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
# libpq for psycopg (Postgres) at runtime; no compiler in the final image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*
# Install from prebuilt wheels (offline, fast, deterministic).
COPY requirements.txt ./
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels
# App source copied AFTER deps so code changes don't bust the deps layer.
COPY app ./app
COPY web ./web
COPY migrations ./migrations
COPY alembic.ini run.py ./
# Drop privileges: run as a fixed non-root uid.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
# Multi-worker Uvicorn, no --reload. Startup runs the config preflight in lifespan
# and REFUSES to boot on unsafe production config. $PORT/$WEB_CONCURRENCY are
# injected by the platform; sane local fallbacks otherwise.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-4} --no-access-log"]
