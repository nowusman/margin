from __future__ import annotations

from margin_eval.structural_compare import compare_gold_corpus_structural


def test_gold_corpus_structural_compare_measures_lightweight_subset() -> None:
    report = compare_gold_corpus_structural()
    assert report.corpus_version.startswith("margin-knowledge-gold")
    assert report.measured_lightweight + report.blocked_rich + report.failed + report.page_mismatches == len(
        report.rows
    )
    assert report.measured_lightweight >= 40
    assert report.blocked_rich == 4
    assert report.failed == 0
    assert report.page_mismatches == 0
    assert all(row.source_sha256 for row in report.rows)
