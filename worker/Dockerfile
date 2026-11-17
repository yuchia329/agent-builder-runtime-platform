# Worker image: the poll loop (`worker` entrypoint). Used by the local stack, and the base for the
# Fargate image in M6. Build context: repo root, so the root .dockerignore applies.
#   docker build -f worker/Dockerfile .

FROM ghcr.io/astral-sh/uv:0.7.3 AS uv

FROM python:3.12.12-slim
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1
WORKDIR /app
# Dependencies first, so code changes do not reinstall them.
COPY worker/pyproject.toml worker/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY worker/src/ src/
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev
RUN useradd --system --uid 10001 app
USER app
ENTRYPOINT ["/app/.venv/bin/worker"]
