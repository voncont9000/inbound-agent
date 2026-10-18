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
CMD ["uv", "run", "uvicorn", "inbound_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
