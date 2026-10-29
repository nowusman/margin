# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY apps/api/requirements.txt /app/apps/api/requirements.txt
COPY workers/requirements.txt /app/workers/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --timeout 300 --retries 10 --no-compile -r /app/workers/requirements.txt

COPY workers /app/workers
COPY packages /app/packages

ENV PYTHONPATH=/app:/app/packages/margin_auth:/app/packages/margin_db:/app/packages/margin_schema:/app/packages/margin_embedding:/app/packages/margin_retrieval:/app/packages/margin_eval:/app/packages/margin_ingest:/app/packages/margin_inference:/app/packages/margin_graph:/app/packages/margin_agent
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

CMD ["python", "-m", "workers.main"]
