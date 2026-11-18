FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    FEEDS_CONFIG=/app/config/feeds.toml

WORKDIR /app
RUN useradd --create-home --uid 1000 collector

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[offload,worker]"

COPY config ./config
USER collector

ENTRYPOINT ["python", "-m", "transit_lakehouse.collector"]
