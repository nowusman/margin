from __future__ import annotations

import os

from celery import Celery


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def build_celery_app() -> Celery:
    broker_url = os.getenv("MARGIN_REDIS_URL", "redis://redis:6379/0")
    result_backend = os.getenv("MARGIN_CELERY_RESULT_BACKEND", broker_url)
    app = Celery("margin", broker=broker_url, backend=result_backend, include=["workers.tasks"])
    app.conf.update(
        task_default_queue="ingestion",
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        task_track_started=True,
        task_always_eager=_bool_env("MARGIN_CELERY_TASK_ALWAYS_EAGER", False),
        worker_prefetch_multiplier=int(os.getenv("MARGIN_CELERY_PREFETCH_MULTIPLIER", "1")),
        worker_concurrency=int(os.getenv("MARGIN_CELERY_CONCURRENCY", "2")),
        worker_pool=os.getenv("MARGIN_CELERY_POOL", "solo"),
    )
    return app


celery_app = build_celery_app()
