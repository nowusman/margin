from __future__ import annotations

from typing import cast


def record_stage_metric(
    trace_metadata: dict[str, object],
    stage: str,
    *,
    count: int,
    latency_ms: int,
    metadata: dict[str, object] | None = None,
) -> None:
    stages = dict(cast(dict[str, dict[str, object]], trace_metadata.get("retrieval_stage_metrics", {})))
    stages[stage] = {"count": count, "latency_ms": latency_ms, **(metadata or {})}
    trace_metadata["retrieval_stage_metrics"] = stages
