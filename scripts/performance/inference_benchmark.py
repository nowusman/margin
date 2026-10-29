"""Governed inference benchmark.

Collects baseline throughput and latency metrics for extraction and answering
workloads. This script is intentionally lightweight for CI and operational probes.
"""

from __future__ import annotations

import json
import os
import platform
import time
from dataclasses import asdict, dataclass
from statistics import median
from urllib.request import Request, urlopen


@dataclass
class BenchmarkResult:
    extraction_tokens_per_sec: float
    answer_latency_p50_ms: float
    answer_latency_p95_ms: float
    batch_throughput_docs_per_min: float
    hardware_profile: dict[str, object]
    provider_mode: str = "synthetic-ci-probe"


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    idx = int((len(values) - 1) * pct)
    return values[idx]


def run_benchmark_probe(samples: int = 20) -> BenchmarkResult:
    endpoint = os.getenv("MARGIN_BENCHMARK_ENDPOINT_URL", "").strip()
    model = os.getenv("MARGIN_BENCHMARK_MODEL", "").strip()
    if endpoint and model:
        return _run_provider_benchmark(endpoint=endpoint, model=model, samples=samples)
    return _run_synthetic_ci_probe(samples=samples)


def _run_provider_benchmark(*, endpoint: str, model: str, samples: int) -> BenchmarkResult:
    latencies_ms: list[float] = []
    tokens_processed = 0
    start = time.perf_counter()
    for index in range(samples):
        prompt = f"Margin benchmark probe {index}: summarize governed evidence in one sentence."
        payload = {
            "model": model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": "Return one short sentence."},
                {"role": "user", "content": prompt},
            ],
        }
        t0 = time.perf_counter()
        _post_json(endpoint.rstrip("/") + "/chat/completions", payload)
        latencies_ms.append((time.perf_counter() - t0) * 1000)
        tokens_processed += max(len(prompt.split()), 1)
    return _result(
        samples=samples,
        start=start,
        latencies_ms=latencies_ms,
        tokens_processed=tokens_processed,
        provider_mode="live-openai-compatible-provider",
    )


def _run_synthetic_ci_probe(samples: int) -> BenchmarkResult:
    latencies_ms: list[float] = []
    tokens_processed = 0
    start = time.perf_counter()
    for _ in range(samples):
        t0 = time.perf_counter()
        time.sleep(0.01)
        latencies_ms.append((time.perf_counter() - t0) * 1000)
        tokens_processed += 200
    return _result(
        samples=samples,
        start=start,
        latencies_ms=latencies_ms,
        tokens_processed=tokens_processed,
        provider_mode="synthetic-ci-probe",
    )


def _post_json(url: str, payload: dict[str, object]) -> None:
    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("MARGIN_BENCHMARK_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urlopen(request, timeout=float(os.getenv("MARGIN_BENCHMARK_TIMEOUT_SECONDS", "30"))) as response:  # noqa: S310
        response.read()


def _result(
    *,
    samples: int,
    start: float,
    latencies_ms: list[float],
    tokens_processed: int,
    provider_mode: str,
) -> BenchmarkResult:
    total_sec = max(time.perf_counter() - start, 1e-6)
    docs_per_min = samples / total_sec * 60
    return BenchmarkResult(
        extraction_tokens_per_sec=tokens_processed / total_sec,
        answer_latency_p50_ms=median(latencies_ms),
        answer_latency_p95_ms=percentile(latencies_ms, 0.95),
        batch_throughput_docs_per_min=docs_per_min,
        hardware_profile={
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "gpu": os.getenv("MARGIN_BENCHMARK_GPU", "not-detected-by-probe"),
        },
        provider_mode=provider_mode,
    )


if __name__ == "__main__":
    result = run_benchmark_probe()
    print(json.dumps(asdict(result), indent=2))
