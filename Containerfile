# Idea Lint — production container (Cloud Run / any OCI runtime)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code and static assets
COPY config.json .
COPY src ./src
COPY templates ./templates
COPY static ./static
COPY scripts ./scripts

# Non-root user with writable runtime and pause directories
RUN useradd --create-home --uid 1000 vb \
    && mkdir -p /app/data /app/data/pauses /app/workspace /app/archive \
    && chown -R vb:vb /app
USER vb

ENV VENTUREBOT_DATA=/app/data \
    VENTUREBOT_WORKSPACE=/app/workspace \
    VENTUREBOT_ARCHIVE_DIR=/app/archive \
    VENTUREBOT_SANDBOX=/tmp/vb-sandbox \
    VENTUREBOT_NO_AUTH=1

EXPOSE 8080

CMD ["sh", "-c", "exec uvicorn src.dashboard:app --host 0.0.0.0 --port ${PORT:-8080}"]
