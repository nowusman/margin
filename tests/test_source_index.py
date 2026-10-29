from __future__ import annotations

from pathlib import Path

from margin_ingest.indexing import LocalSourceTreeAdapter, build_source_index

FIXTURES = Path("tests/fixtures/source_ingestion")


def test_local_source_tree_adapter_builds_section_tree_over_markdown_not_git_tree() -> None:
    markdown = (FIXTURES / "architecture.md").read_text(encoding="utf-8")

    source_index = LocalSourceTreeAdapter().build(markdown, document_id="doc-1")

    assert [section.title for section in source_index.sections] == ["Customer Platform HLD", "Context", "Decisions"]
    assert source_index.sections[1].path == ("Customer Platform HLD", "Context")
    assert all("fixtures" not in part for section in source_index.sections for part in section.path)


def test_source_index_generates_addressable_spans_for_core_block_types() -> None:
    markdown = (FIXTURES / "architecture.md").read_text(encoding="utf-8")

    source_index = build_source_index(markdown, document_id="doc-1")
    span_types = {span.span_type for span in source_index.spans}

    assert {"heading", "paragraph", "list_item", "table_header", "table_row", "figure", "code"}.issubset(span_types)
    assert all(span.start_line <= span.end_line for span in source_index.spans)
    assert all(span.start_offset <= span.end_offset for span in source_index.spans)
    assert any(span.page == 1 for span in source_index.spans)


def test_source_index_uses_stable_node_ids_and_reports_uncertain_structure() -> None:
    markdown = "# Context\n\nEvidence.\n\n### Deep Heading\n\nNested evidence.\n"

    first = build_source_index(markdown, document_id="doc-1")
    second = build_source_index(markdown, document_id="doc-1")

    assert [section.section_id for section in first.sections] == [section.section_id for section in second.sections]
    assert [span.span_id for span in first.spans] == [span.span_id for span in second.spans]
    assert "skipped_heading_level" in first.warnings


def test_final_section_range_includes_the_last_line() -> None:
    markdown = "# First\n\nBody\n\n# Last\n\nFinal line"

    source_index = build_source_index(markdown, document_id="doc-final")

    assert source_index.sections[-1].end_offset == len(markdown)
    section_text = markdown[source_index.sections[-1].start_offset : source_index.sections[-1].end_offset]
    assert section_text.endswith("Final line")


def test_source_index_keeps_multipage_tables_and_ignores_repeated_headers() -> None:
    markdown = (
        "<!-- page: 1 -->\n"
        "| System | Owner |\n"
        "| --- | --- |\n"
        "| Billing API | Platform Team |\n"
        "<!-- page: 2 -->\n"
        "| System | Owner |\n"
        "| Inventory API | Operations Team |\n"
    )

    source_index = build_source_index(markdown, document_id="doc-table")

    parents = [span for span in source_index.spans if span.span_type == "table"]
    rows = [span for span in source_index.spans if span.metadata.get("table_role") == "row"]
    repeated_headers = [span for span in source_index.spans if span.metadata.get("table_role") == "repeated_header"]

    assert len(parents) == 1
    assert [row.page for row in rows] == [1, 2]
    assert [row.metadata["row_index"] for row in rows] == [1, 2]
    assert len(repeated_headers) == 1
    assert len({span.span_id for span in source_index.spans}) == len(source_index.spans)
