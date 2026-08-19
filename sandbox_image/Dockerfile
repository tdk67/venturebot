# VentureBot sandbox image — hostile-code isolation for generated tests.
# No network at runtime (--network=none), unprivileged user, read-only FS.
FROM python:3.11-slim

# Minimal, pinned test deps only. NO project secrets baked in.
RUN pip install --no-cache-dir pytest==8.3.4

# Run as unprivileged numeric user (65534 = nobody in most distros).
# Using numeric UID/GID avoids useradd conflicts with pre-existing nogroup.
USER 65534:65534

# Tests are mounted read-only at /ws by the sandbox runner
WORKDIR /ws

CMD ["python", "-m", "pytest", "/ws", "-v", "--tb=short"]
