# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        graphviz \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        libreoffice-impress \
        libreoffice-writer \
        plantuml \
    && rm -rf /var/lib/apt/lists/*

COPY apps/api/requirements.txt /app/apps/api/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --timeout 300 --retries 10 --no-compile -r /app/apps/api/requirements.txt

COPY apps/api /app/apps/api
COPY workers /app/workers
COPY packages /app/packages

ENV PYTHONPATH=/app:/app/apps/api:/app/packages/margin_auth:/app/packages/margin_db:/app/packages/margin_schema:/app/packages/margin_embedding:/app/packages/margin_retrieval:/app/packages/margin_eval:/app/packages/margin_ingest:/app/packages/margin_inference:/app/packages/margin_graph:/app/packages/margin_agent
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

ENV WEB_CONCURRENCY=1

CMD ["sh", "-c", "alembic -c packages/margin_db/alembic.ini upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY}"]
