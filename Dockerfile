# syntax=docker/dockerfile:1

# ---- Build stage: install dependencies ----
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app

# Compile bytecode for faster startup; copy (not symlink) files from uv's cache
# into the venv so it can be copied to the runtime stage; use the image's Python.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

# Install only third-party dependencies, straight from the lock file. This layer
# is cached and only rebuilt when pyproject.toml or uv.lock change. The app runs
# from src/ directly, so the project itself doesn't need installing.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --locked --no-dev --no-install-project


# ---- Runtime stage: slim image with only the venv and app code ----
FROM python:3.12-slim-bookworm

WORKDIR /app

# Run as an unprivileged user (UID 1000, which some hosts such as Hugging
# Face Spaces expect).
RUN groupadd --gid 1000 app && useradd --uid 1000 --gid app --home-dir /app app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app src/server.py ./src/server.py

# Knowledge base read by get_retriever() (relative to WORKDIR), embedded at
# startup.
COPY --chown=app:app docs_angular/ ./docs_angular/

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"PORT\", \"8000\")}/api/health', timeout=3)"

# Provide config at runtime (.env is not copied into the image), e.g.
#   docker run -e HF_API_KEY=... -e CORS_ORIGINS=https://your-frontend.com ...
# Listens on $PORT when the host sets it (e.g. Render), otherwise 8000.
CMD ["sh", "-c", "exec fastapi run src/server.py --host 0.0.0.0 --port ${PORT:-8000}"]
