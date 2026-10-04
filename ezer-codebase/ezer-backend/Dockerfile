# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml ./
COPY app ./app
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --disable-pip-version-check .

FROM python:3.12-slim AS runtime
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN groupadd --system --gid 10001 ezer \
    && useradd --system --uid 10001 --gid ezer --no-create-home --shell /usr/sbin/nologin ezer \
    && mkdir -p /app/data \
    && chown -R ezer:ezer /app
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
USER ezer
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
