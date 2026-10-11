FROM ghcr.io/astral-sh/uv:0.10.9 AS uv
FROM python:3.13-slim-bookworm

COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    SUMER_OBSERVE=false \
    PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY web ./web
COPY results ./results
RUN uv sync --frozen --no-dev && mkdir -p /app/.cache

EXPOSE 10000
CMD ["sumer", "up"]
