"""Margin queue worker entrypoint for the unified ingestion lifecycle."""

from __future__ import annotations

import os
from pathlib import Path

from workers.celery_app import celery_app

LOG_LEVEL = os.getenv("MARGIN_WORKER_LOG_LEVEL", "INFO").strip().upper()
WORKER_POOL = os.getenv("MARGIN_CELERY_POOL", "solo").strip().lower()
WORKER_QUEUES = ("ingestion", "extraction", "embedding", "review", "maintenance", "export")


def _bootstrap_sys_path() -> None:
    import sys

    packages_dir = Path(__file__).resolve().parents[1] / "packages"
    if packages_dir.exists():
        for pkg_dir in packages_dir.iterdir():
            if pkg_dir.is_dir() and str(pkg_dir) not in sys.path:
                sys.path.insert(0, str(pkg_dir))


def _run_worker() -> None:
    _bootstrap_sys_path()
    print(f"[margin-worker] starting queues={','.join(WORKER_QUEUES)}")
    worker = celery_app.Worker(loglevel=LOG_LEVEL, pool=WORKER_POOL, queues=",".join(WORKER_QUEUES), traceback=True)
    worker.start()


def validate_converter_dependencies() -> None:
    _bootstrap_sys_path()
    from margin_ingest.converters import validate_converter_dependencies as _validate

    _validate()


def main() -> None:
    validate_converter_dependencies()
    _run_worker()


if __name__ == "__main__":
    main()
