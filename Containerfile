# VentureBot — production container (Cloud Run / any OCI runtime)
# Build:  docker build -t venturebot .
# Run:    docker run -p 8080:8080 -e GOOGLE_API_KEY=... venturebot

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY src ./src
COPY templates ./templates
COPY static ./static
COPY scripts ./scripts

# Non-root user; writable runtime dirs
RUN useradd --create-home --uid 1000 vb \
    && mkdir -p /app/data /app/workspace /app/archive \
    && chown -R vb:vb /app
USER vb

# Runtime paths inside the container
ENV VENTUREBOT_DATA=/app/data \
    VENTUREBOT_WORKSPACE=/app/workspace \
    VENTUREBOT_ARCHIVE_DIR=/app/archive \
    VENTUREBOT_SANDBOX=/tmp/vb-sandbox

# Cloud Run injects PORT (default 8080 elsewhere)
EXPOSE 8080

# Optional state persistence: restore latest snapshot from GCS before boot,
# keep syncing while running (see notes/GCP_DEPLOYMENT.md). No-op unless
# GCS_DATA_BUCKET is set.
CMD ["sh", "-c", "\
    if [ -n \"$GCS_DATA_BUCKET\" ]; then \
      python scripts/data_snapshot.py restore || echo \"[boot] no snapshot restored\"; \
    fi; \
    if [ -n \"$GCS_DATA_BUCKET\" ]; then \
      python scripts/data_snapshot.py watch &  \
    fi; \
    exec uvicorn src.dashboard:app --host 0.0.0.0 --port ${PORT:-8080}"]
