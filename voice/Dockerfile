FROM ghcr.io/astral-sh/uv:0.6.3 AS uv

FROM python:3.12-slim

COPY --from=uv /uv /uvx /bin/
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
COPY core /app/core
COPY voice/pyproject.toml voice/uv.lock /app/voice/
WORKDIR /app/voice
RUN uv sync --locked --no-dev --no-install-project
COPY voice /app/voice
RUN uv sync --locked --no-dev \
    && groupadd --system voice-app \
    && useradd --create-home --uid 10001 --gid voice-app voice \
    && mkdir -p /app/voice/spool /app/voice/runs /app/voice/eval/results /app/voice/eval/audio \
    && chown -R voice:voice-app /app/voice/spool /app/voice/runs /app/voice/eval/results /app/voice/eval/audio
ENV PATH=/opt/venv/bin:$PATH
USER voice
WORKDIR /app/voice

CMD ["python", "phone_server.py", "--host", "0.0.0.0", "--port", "7861"]
