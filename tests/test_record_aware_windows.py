"""Record-aware multi-span extraction tests."""

from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from margin_db.base import Base
from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement
from margin_db.user_models import Domain
from tests.helpers.catalog import catalog_entity
from workers.extract.inference import ExtractionRequest, _EligibleElementIndex
from workers.extract.windows import EligibleElement, build_extraction_windows


def _document() -> SourceDocument:
    return SourceDocument(
        id="src_records",
        title="Architecture",
        object_key="o",
        media_type="text/markdown",
        sha256="a" * 64,
        domain_id="domain_1",
        category_id="category_1",
    )


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _seed_heading_and_body(session: Session) -> None:
    document = _document()
    session.add(Domain(id="domain_1", name="Domain One"))
    session.add(catalog_entity(entity_id="category_1", preferred_label="Design Document", ontology_type="category"))
    session.add(document)
    version = SourceDocumentVersion(id="v1", source_document_id=document.id, version_sha256="b" * 64)
    session.add(version)
    session.add_all(
        [
            SourceElement(
                id="elem_heading",
                source_version_id=version.id,
                kind="heading",
                ordinal=0,
                text="The platform uses PostgreSQL.",
                char_start=0,
                char_end=33,
                checksum="h1",
            ),
            SourceElement(
                id="elem_body",
                source_version_id=version.id,
                kind="paragraph",
                ordinal=1,
                text="PostgreSQL stores architecture evidence.",
                char_start=34,
                char_end=72,
                checksum="b1",
            ),
        ]
    )
    session.flush()


def test_heading_plus_body_form_one_record_window() -> None:
    session = _session()
    _seed_heading_and_body(session)
    document = session.get(SourceDocument, "src_records")
    version = session.get(SourceDocumentVersion, "v1")
    elements = list(session.scalars(select(SourceElement)).all())

    windows = build_extraction_windows(
        document=document, version=version, elements=elements, max_tokens=2000, neighbor_tokens=0
    )

    assert len(windows) == 1
    window = windows[0]
    assert set(window.primary_source_element_ids) == {"elem_heading", "elem_body"}
    assert [elem.source_element_id for elem in window.eligible_citable_elements] == ["elem_heading", "elem_body"]
    assert "The platform uses PostgreSQL." in window.text
    assert "PostgreSQL stores architecture evidence." in window.text


def test_two_paragraph_decision_cites_both_required_spans() -> None:
    session = _session()
    _seed_heading_and_body(session)
    document = session.get(SourceDocument, "src_records")
    version = session.get(SourceDocumentVersion, "v1")
    heading = session.get(SourceElement, "elem_heading")
    body = session.get(SourceElement, "elem_body")
    elements = list(session.scalars(select(SourceElement)).all())
    windows = build_extraction_windows(
        document=document, version=version, elements=elements, max_tokens=2000, neighbor_tokens=0
    )
    request = ExtractionRequest(
        window_id=windows[0].window_id,
        source_element_id=heading.id,
        text=windows[0].text,
        content_type="heading",
        page_number=None,
        section_heading=None,
        section_path="Integration Scope",
        preceding_context=(),
        following_context=(),
        document_metadata={"title": document.title},
        primary_source_element_ids=windows[0].primary_source_element_ids,
        eligible_citable_elements=tuple(vars(s) for s in windows[0].eligible_citable_elements),
    )
    index = _EligibleElementIndex.from_request(request)
    assert index.accepts(type("E", (), {"source_element_id": heading.id})())
    assert index.accepts(type("E", (), {"source_element_id": body.id})())


def test_table_record_chunking_repeats_header_per_chunk() -> None:
    session = _session()
    _seed_heading_and_body(session)
    document = session.get(SourceDocument, "src_records")
    version = session.get(SourceDocumentVersion, "v1")
    header = "| System | Owner |"
    aligner = "|---|---|"
    rows = [f"| Service {index} | Team {index} |" for index in range(40)]
    table_text = "\n".join([header, aligner, *rows])
    element = SourceElement(
        id="elem_table",
        source_version_id=version.id,
        kind="table",
        ordinal=2,
        text=table_text,
        char_start=0,
        char_end=len(table_text),
        checksum="t1",
    )
    session.add(element)
    session.flush()

    windows = build_extraction_windows(
        document=document, version=version, elements=[element], max_tokens=60, neighbor_tokens=0
    )

    assert len(windows) > 1
    for window in windows:
        # Every chunk must repeat the table header so the column semantics
        # are preserved across all chunks.
        assert header in window.text
        assert aligner in window.text
    # No chunk should duplicate the header (header lines should appear once
    # at the start of each chunk, not twice).
    for window in windows:
        assert window.text.count(header) == 1


def test_figure_caption_plus_ocr_form_one_record_window() -> None:
    session = _session()
    document = _document()
    session.add(Domain(id="domain_1", name="Domain One"))
    session.add(catalog_entity(entity_id="category_1", preferred_label="Design Document", ontology_type="category"))
    session.add(document)
    version = SourceDocumentVersion(id="v1", source_document_id=document.id, version_sha256="b" * 64)
    session.add(version)
    session.add_all(
        [
            SourceElement(
                id="elem_fig_caption",
                source_version_id=version.id,
                kind="figure_caption",
                ordinal=0,
                text="Figure 1: Customer Platform architecture.",
                char_start=0,
                char_end=44,
                checksum="fc1",
            ),
            SourceElement(
                id="elem_ocr",
                source_version_id=version.id,
                kind="image_ocr",
                ordinal=1,
                text="Customer Platform -> PostgreSQL",
                char_start=45,
                char_end=78,
                checksum="ocr1",
            ),
        ]
    )
    session.flush()

    windows = build_extraction_windows(
        document=document,
        version=version,
        elements=list(session.scalars(select(SourceElement)).all()),
        max_tokens=2000,
        neighbor_tokens=0,
    )

    assert len(windows) == 1
    window = windows[0]
    assert set(window.primary_source_element_ids) == {"elem_fig_caption", "elem_ocr"}
    eligible_ids = {elem.source_element_id for elem in window.eligible_citable_elements}
    assert eligible_ids == {"elem_fig_caption", "elem_ocr"}


def test_unrelated_neighbouring_context_cannot_be_cited() -> None:
    session = _session()
    _seed_heading_and_body(session)
    unrelated = SourceElement(
        id="elem_unrelated",
        source_version_id="v1",
        kind="paragraph",
        ordinal=2,
        text="This should not be citable.",
        char_start=0,
        char_end=28,
        checksum="u1",
    )
    session.add(unrelated)
    session.flush()

    request = ExtractionRequest(
        window_id="window_1",
        source_element_id="elem_heading",
        text="Combined window text",
        content_type="heading",
        page_number=None,
        section_heading="Integration Scope",
        section_path="Integration Scope",
        preceding_context=(),
        following_context=(),
        document_metadata={"title": "Test"},
        eligible_citable_elements=tuple(
            vars(elem)
            for elem in (
                EligibleElement(
                    source_element_id="elem_heading",
                    source_page_id=None,
                    element_type="heading",
                    page_number=None,
                    text="The platform uses PostgreSQL.",
                    role="primary",
                    text_role="heading",
                ),
                EligibleElement(
                    source_element_id="elem_body",
                    source_page_id=None,
                    element_type="paragraph",
                    page_number=None,
                    text="PostgreSQL stores architecture evidence.",
                    role="primary",
                    text_role="body",
                ),
            )
        ),
    )
    index = _EligibleElementIndex.from_request(request)
    assert not index.accepts(type("E", (), {"source_element_id": unrelated.id})())


def test_reprocessing_produces_deterministic_windows() -> None:
    session = _session()
    _seed_heading_and_body(session)
    document = session.get(SourceDocument, "src_records")
    version = session.get(SourceDocumentVersion, "v1")
    elements = list(session.scalars(select(SourceElement)).all())
    first = build_extraction_windows(
        document=document, version=version, elements=elements, max_tokens=2000, neighbor_tokens=0
    )
    second = build_extraction_windows(
        document=document, version=version, elements=elements, max_tokens=2000, neighbor_tokens=0
    )
    assert [w.window_id for w in first] == [w.window_id for w in second]
    assert [w.text for w in first] == [w.text for w in second]


def test_structured_table_kinds_without_table_id_stay_on_table_rows_path() -> None:
    session = _session()
    document = _document()
    session.add(Domain(id="domain_1", name="Domain One"))
    session.add(catalog_entity(entity_id="category_1", preferred_label="Design Document", ontology_type="category"))
    session.add(document)
    version = SourceDocumentVersion(id="v1", source_document_id=document.id, version_sha256="b" * 64)
    session.add(version)
    header = SourceElement(
        id="elem_orphan_header",
        source_version_id=version.id,
        kind="table_header",
        ordinal=0,
        text="| System | Owner |",
        checksum="th1",
        metadata_={},
    )
    row = SourceElement(
        id="elem_orphan_row",
        source_version_id=version.id,
        kind="table_row",
        ordinal=1,
        text="| Billing API | Platform |",
        checksum="tr1",
        metadata_={"row_index": 0},
    )
    cell = SourceElement(
        id="elem_orphan_cell",
        source_version_id=version.id,
        kind="table_cell",
        ordinal=2,
        text="Billing API",
        checksum="tc1",
        metadata_={"row_index": 0, "column_index": 0},
    )
    session.add_all([header, row, cell])
    session.flush()

    windows = build_extraction_windows(
        document=document, version=version, elements=[header, row, cell], max_tokens=500, neighbor_tokens=0
    )

    assert len(windows) == 1
    window = windows[0]
    assert window.strategy == "table_rows"
    assert window.content_type == "table_rows"
    assert window.source_element_id == row.id
    assert all(item.strategy != "text_overlap" for item in windows)
    assert header.id not in {window.source_element_id for window in windows}
    assert cell.id not in {window.source_element_id for window in windows}
