from __future__ import annotations

import logging
from typing import Any

from margin_retrieval.local_runtime import build_retrieval_service

logger = logging.getLogger(__name__)


def configure_retrieval_service(app: Any) -> None:
    try:
        app.state.retrieval_service = build_retrieval_service()
        app.state.retrieval_service_error = ""
    except (OSError, RuntimeError) as exc:
        app.state.retrieval_service = None
        app.state.retrieval_service_error = str(exc)
        logger.warning("Retrieval service was not configured: %s", exc)
