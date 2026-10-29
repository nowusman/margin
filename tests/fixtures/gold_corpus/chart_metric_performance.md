# API Gateway Performance Metrics

Chart 1: P99 Latency vs Concurrency Benchmark
[Chart Element: kind="chart", chart_type="bar", asset_key="chart-p99-latency", bbox=[60, 100, 650, 420]]

Tabular Backing Data:
| Concurrency Level | P99 Latency (ms) | Error Rate (%) |
| 100 clients | 45 ms | 0.00% |
| 1000 clients | 120 ms | 0.02% |
| 5000 clients | 480 ms | 0.15% |

Summary Assertion:
API gateway P99 latency remains under 500 milliseconds up to 5000 concurrent clients.
Evidence kind for chart extractions must be recorded as derived_visual with tabular anchor.
