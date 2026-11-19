# Tool gateway image: MCP over Streamable HTTP on port 8090 (`gateway` entrypoint). Used by the
# local stack; the Lambda packaging comes in M6. Build context: repo root, so the root
# .dockerignore applies.
#   docker build -f tool-gateway/Dockerfile .

FROM ghcr.io/astral-sh/uv:0.7.3 AS uv

FROM python:3.12.12-slim
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1 \
    CONTRACTS_DIR=/app/contracts CONNECTORS_DIR=/app/connectors \
    GATEWAY_HOST=0.0.0.0 GATEWAY_PORT=8090
WORKDIR /app
# Dependencies first, so code changes do not reinstall them.
COPY tool-gateway/pyproject.toml tool-gateway/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY tool-gateway/src/ src/
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev
# Shapes and connector definitions the gateway loads at startup.
COPY contracts/*.schema.json contracts/
COPY tool-gateway/connectors/ connectors/
RUN useradd --system --uid 10001 app
USER app
EXPOSE 8090
HEALTHCHECK --interval=2s --timeout=2s --retries=60 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=1)"]
ENTRYPOINT ["/app/.venv/bin/gateway"]
