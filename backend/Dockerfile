FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.6.3 /uv /uvx /bin/
WORKDIR /app
COPY core /app/core
COPY voice/config/templates /app/voice/config/templates
COPY voice/config/prices.yaml /app/voice/config/prices.yaml
COPY backend /app/backend
WORKDIR /app/backend
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
RUN uv sync --locked --no-dev
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/backend/var \
    && chown -R app:app /app/backend/var
ENV PATH=/opt/venv/bin:$PATH
CMD ["/opt/venv/bin/uvicorn", "inbound_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
