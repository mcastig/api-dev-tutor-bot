# syntax=docker/dockerfile:1

# ---- Build stage: install dependencies and download the embedding model ----
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

# Download the embedding model at build time so containers don't download it
# on every start. Keep the model name in sync with server.py.
ENV HF_HOME=/app/.cache/huggingface
RUN /app/.venv/bin/python -c \
    "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"


# ---- Runtime stage: slim image with only the venv, model, and app code ----
FROM python:3.12-slim-bookworm

WORKDIR /app

# Run as an unprivileged user.
RUN groupadd --system app && useradd --system --gid app --home-dir /app app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/.cache/huggingface /app/.cache/huggingface
COPY --chown=app:app src/server.py ./src/server.py

# Knowledge base read by get_retriever() (relative to WORKDIR), embedded at
# startup.
COPY --chown=app:app docs_angular/ ./docs_angular/

# No HF_HUB_OFFLINE here: it would also block the LLM calls to Hugging Face
# Inference Providers. The embedding model is still loaded from the cache above.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/app/.cache/huggingface

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"

# Provide config at runtime (.env is not copied into the image), e.g.
#   docker run -e HF_API_KEY=... -e CORS_ORIGINS=https://your-frontend.com ...
CMD ["fastapi", "run", "src/server.py", "--host", "0.0.0.0", "--port", "8000"]
