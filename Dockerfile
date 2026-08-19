# VentureBot — unified dashboard + self-improvement layer (Stage 1: VPS / Stage 3: Cloud Run)
#
# Single container runs the FastAPI dashboard (SSE, SSO, HITL gates, memory API).
# Phase 1 agents run in-process via google-adk; no separate Agent Engine needed
# for the Stage-1/VPS and hackathon-demo deployment.
#
# Secrets are injected at runtime via environment (never baked into the image).
# State (state.json, data/*.db) is NOT in the image — mount a volume or use GCP
# storage in production.

FROM python:3.11-slim

# System deps needed by google-adk / cryptography wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY venturebot/ ./venturebot/
COPY templates/ ./templates/
COPY static/ ./static/

# Runtime state is written here (mount a volume in production)
RUN mkdir -p /app/data /app/workspace /app/sandbox

# Point runtime state at the writable dirs (BASE_DIR is root-owned)
ENV PYTHONUNBUFFERED=1 \
    PORT=8080 \
    VENTUREBOT_STATE=/app/data/state.json \
    VENTUREBOT_DATA=/app/data \
    VENTUREBOT_WORKSPACE=/app/workspace \
    VENTUREBOT_SANDBOX=/app/sandbox

# Non-root user for the web server (defense in depth; the sandbox already
# drops to 65534 for generated-code execution)
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app/data /app/workspace /app/sandbox
USER appuser

EXPOSE 8080

CMD ["uvicorn", "venturebot.dashboard:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
