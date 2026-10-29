from __future__ import annotations

from pathlib import Path


def test_sample_data_fixture_set_exists() -> None:
    root = Path("tests/fixtures/sample-data")

    required = [
        "domain.yaml",
        "design-docs/customer-platform-hld.md",
        "adrs/adr-0001-use-postgresql.md",
        "risks/risk-0001-stale-architecture-docs.md",
        "waivers/waiver-0001-temporary-doc-gap.md",
        "technical-debt/debt-0001-placeholder-services.md",
        "diagrams/customer-platform.dsl",
    ]

    for relative_path in required:
        path = root / relative_path
        assert path.exists(), f"Missing sample-data fixture: {path}"
        assert path.read_text(encoding="utf-8").strip()
