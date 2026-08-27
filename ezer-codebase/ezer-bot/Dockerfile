# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.4.25@sha256:75ea96bbba2e43a11c90173f1b963eb2354a18e7673d2b0ee8e9428fa4afec7a AS uv
FROM python:3.14-slim@sha256:83ff1d245a3d57d04152252d3ef9cb361494d0b3395abd65a5ebe91c401c8e83 AS builder

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY --from=uv /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.14-slim@sha256:83ff1d245a3d57d04152252d3ef9cb361494d0b3395abd65a5ebe91c401c8e83 AS runtime
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    LANGGRAPH_STRICT_MSGPACK=true
WORKDIR /app
RUN groupadd --system ezer && useradd --system --gid ezer --home /app ezer \
    && mkdir -p /data && chown -R ezer:ezer /app /data
COPY --from=builder --chown=ezer:ezer /app/.venv /app/.venv
USER ezer
EXPOSE 8080
ENTRYPOINT ["ezer"]
CMD ["api", "--host", "0.0.0.0", "--port", "8080"]
