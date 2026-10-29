from __future__ import annotations

import json
from decimal import Decimal

from sqlalchemy import select

from margin_db.extraction_models import ExtractionRun
from margin_db.review_models import ReviewTask
from margin_db.source_models import (
    SourceDocument,
    SourceDocumentVersion,
    SourceElement,
    SourceElementOutcome,
    UnmappedExtraction,
)
from margin_inference import (
    InferenceClient,
    InferenceResponse,
    InferenceTimeoutError,
    InferenceUnavailableError,
    InferenceUsage,
)
from margin_ingest.contracts import NormalizedElement
from margin_ingest.vision import process_version_visual_elements, select_visual_element
from tests.helpers.source_scope import TEST_CATEGORY_ID
from tests.helpers.vision import FIXTURES, _configure_vision_model

# =========================================================================
# 1. Element & Region Selection Rules
# =========================================================================


def test_element_selection_rules_preserve_docling_valid_text():
    # Valid normal paragraph
    p = NormalizedElement(
        element_id="elem_p1",
        kind="paragraph",
        text="This is valid paragraph text extracted by Docling.",
        ordinal=0,
        confidence=0.95,
    )
    should_proc, reason = select_visual_element(p)
    assert not should_proc
    assert reason is None

    # Valid heading
    h = NormalizedElement(
        element_id="elem_h1", kind="heading", text="Chapter 1: Architecture Overview", ordinal=1, confidence=0.99
    )
    should_proc, reason = select_visual_element(h)
    assert not should_proc
    assert reason is None

    # Normal table with content
    t = NormalizedElement(
        element_id="elem_t1",
        kind="table",
        text="| Service | Port |\n| --- | --- |\n| API | 8000 |",
        ordinal=2,
        confidence=0.9,
    )
    should_proc, reason = select_visual_element(t)
    assert not should_proc
    assert reason is None


def test_element_selection_rules_select_targeted_visual_regions():
    # Chart
    chart = NormalizedElement(element_id="elem_c1", kind="chart", text="", ordinal=0, asset_key="chart_asset")
    should_proc, reason = select_visual_element(chart)
    assert should_proc
    assert reason == "chart"

    # Diagram
    diag = NormalizedElement(element_id="elem_d1", kind="diagram", text="", ordinal=1, asset_key="diag_asset")
    should_proc, reason = select_visual_element(diag)
    assert should_proc
    assert reason == "diagram"

    # Screenshot
    ss = NormalizedElement(element_id="elem_s1", kind="screenshot", text="", ordinal=2, asset_key="ss_asset")
    should_proc, reason = select_visual_element(ss)
    assert should_proc
    assert reason == "screenshot"

    # Ambiguous table
    ambig_t = NormalizedElement(
        element_id="elem_at1", kind="table", text="", ordinal=3, asset_key="table_asset", metadata={"ambiguous": True}
    )
    should_proc, reason = select_visual_element(ambig_t)
    assert should_proc
    assert reason == "ambiguous_table"

    # Difficult scan
    scan = NormalizedElement(
        element_id="elem_scan1", kind="scan", text="smudged text", ordinal=4, metadata={"scan_quality": "difficult"}
    )
    should_proc, reason = select_visual_element(scan)
    assert should_proc
    assert reason == "difficult_scan"

    # Unreadable text with low confidence and asset
    unread = NormalizedElement(
        element_id="elem_u1", kind="paragraph", text="???", ordinal=5, confidence=0.2, asset_key="crop_asset"
    )
    should_proc, reason = select_visual_element(unread)
    assert should_proc
    assert reason == "unreadable_text"

    # Image without text
    img = NormalizedElement(element_id="elem_img1", kind="image", text="", ordinal=6, asset_key="img_asset")
    should_proc, reason = select_visual_element(img)
    assert should_proc
    assert reason == "visual_figure"


# =========================================================================
# 2. Configured Fake Vision Provider Test (Structured Output)
# =========================================================================


def test_configured_fake_vision_provider_produces_structured_output(session_factory, monkeypatch):
    calls = []

    structured_json = json.dumps(
        {
            "region_id": "elem_diagram_1",
            "element_kind": "diagram",
            "transcription": "Client -> API Gateway -> Database",
            "factual_description": (
                "A technical architecture diagram depicting Client requests flowing through an API Gateway "
                "to a PostgreSQL Database."
            ),
            "candidate_assertions": [
                "Client communicates through API Gateway",
                "API Gateway connects directly to PostgreSQL Database",
            ],
            "confidence": 0.94,
        }
    )

    def fake_chat_completions(self, messages, **kwargs):
        calls.append((self, messages, kwargs))
        assert self.model == "test-vision-model"
        assert messages[0]["role"] == "system"
        assert "candidate_assertions" in messages[0]["content"]
        assert messages[1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
        return InferenceResponse(
            content=structured_json,
            usage=InferenceUsage(prompt_tokens=150, completion_tokens=80, total_tokens=230),
            raw_response={"choices": [{"message": {"content": structured_json}}]},
        )

    monkeypatch.setattr(InferenceClient, "chat_completions", fake_chat_completions)

    with session_factory() as session:
        model = _configure_vision_model(session)

        # Create source document, version, and element
        doc = SourceDocument(
            id="doc_vis_1",
            title="Architecture Overview",
            object_key="obj_vis_1",
            media_type="application/pdf",
            sha256="sha_vis_1",
            domain_id="OSS",
            category_id=TEST_CATEGORY_ID,
            sensitivity="internal",
        )
        session.add(doc)
        session.flush()

        ver = SourceDocumentVersion(id="ver_vis_1", source_document_id=doc.id, version_sha256="sha_ver_1")
        session.add(ver)
        session.flush()

        elem = SourceElement(
            id="elem_diagram_1",
            source_version_id=ver.id,
            kind="diagram",
            ordinal=0,
            text="",
            asset_key="asset_diagram_1",
            bounding_box={"x0": 10, "y0": 20, "x1": 200, "y1": 150},
        )
        session.add(elem)
        session.flush()

        # Execute vision processing
        png_bytes = (FIXTURES / "sample.png").read_bytes()
        outcomes = process_version_visual_elements(
            session,
            source_document=doc,
            source_version=ver,
            object_store=None,
            visual_assets=[type("Asset", (), {"asset_key": "asset_diagram_1", "data": png_bytes})()],
        )

        assert len(outcomes) == 1
        assert outcomes[0].status == "success"
        assert outcomes[0].result is not None
        assert outcomes[0].result.transcription == "Client -> API Gateway -> Database"
        assert outcomes[0].result.confidence == 0.94

        cached_outcomes = process_version_visual_elements(
            session,
            source_document=doc,
            source_version=ver,
            object_store=None,
            visual_assets=[type("Asset", (), {"asset_key": "asset_diagram_1", "data": png_bytes})()],
        )
        assert cached_outcomes[0].status == "success"
        assert len(calls) == 1

        # Verify SourceElement update
        assert elem.text is not None
        assert "Client -> API Gateway -> Database" in elem.text
        assert "API Gateway to a PostgreSQL Database" in elem.text
        assert elem.confidence == Decimal("0.94")
        assert elem.converter_method == "vision_ocr"
        assert elem.metadata_["quarantined"] is False
        assert elem.metadata_["is_derived_visual"] is True
        assert elem.metadata_["evidence_kind"] == "derived_visual"

        # Traceability record verification
        trace = elem.metadata_["vision_traceability"]
        assert trace["model_id"] == model.id
        assert trace["is_derived_visual"] is True
        assert trace["parsed_response"]["confidence"] == 0.94
        assert trace["usage"]["total_tokens"] == 230

        # Unmapped extractions verification
        unmapped = list(
            session.scalars(select(UnmappedExtraction).where(UnmappedExtraction.source_element_id == elem.id)).all()
        )
        assert len(unmapped) == 2
        assert {u.raw_payload["statement"] for u in unmapped} == {
            "Client communicates through API Gateway",
            "API Gateway connects directly to PostgreSQL Database",
        }
        assert all(u.status == "open" for u in unmapped)
        assert all(u.kind == "claim" for u in unmapped)

        # No quarantine task created
        review_tasks = list(session.scalars(select(ReviewTask)).all())
        assert len(review_tasks) == 0


# =========================================================================
# 3. Missing Vision Provider Test (Quarantine Path, No Crash, No Invented Text)
# =========================================================================


def test_missing_vision_provider_quarantines_and_creates_review_task(session_factory, monkeypatch):
    with session_factory() as session:
        # NO vision_ocr model in DB
        doc = SourceDocument(
            id="doc_vis_no_model",
            title="Diagram Document",
            object_key="obj_no_model",
            media_type="image/png",
            sha256="sha_no_model",
            domain_id="OSS",
            category_id=TEST_CATEGORY_ID,
            sensitivity="internal",
        )
        session.add(doc)
        session.flush()

        ver = SourceDocumentVersion(id="ver_vis_no_model", source_document_id=doc.id, version_sha256="sha_ver_no_model")
        session.add(ver)
        session.flush()

        original_text = ""
        elem = SourceElement(
            id="elem_chart_no_model",
            source_version_id=ver.id,
            kind="chart",
            ordinal=0,
            text=original_text,
            asset_key="chart_asset_key",
        )
        session.add(elem)
        session.flush()

        png_bytes = (FIXTURES / "sample.png").read_bytes()
        outcomes = process_version_visual_elements(
            session, source_document=doc, source_version=ver, object_store=None, source_bytes=png_bytes
        )
        process_version_visual_elements(
            session, source_document=doc, source_version=ver, object_store=None, source_bytes=png_bytes
        )

        # Must not crash
        assert len(outcomes) == 1
        assert outcomes[0].status == "quarantined"
        assert outcomes[0].error_kind == "missing_model"

        # Element must NOT have invented placeholder text
        assert elem.text == original_text
        assert elem.metadata_["quarantined"] is True
        assert elem.metadata_["error_kind"] == "missing_model"

        # ReviewTask must be created
        tasks = list(
            session.scalars(
                select(ReviewTask).where(ReviewTask.task_type == "vision_quarantine", ReviewTask.target_id == elem.id)
            ).all()
        )
        assert len(tasks) == 1
        task = tasks[0]
        assert task.status == "open"
        assert task.target_type == "source_element"
        assert "no active vision_ocr model configured" in task.reason
        assert len(task.evidence) == 1

        # Ingest-scoped ExtractionRun + SourceElementOutcome must be durable
        runs = list(
            session.scalars(
                select(ExtractionRun).where(
                    ExtractionRun.source_version_id == ver.id, ExtractionRun.mode == "vision_ingest"
                )
            ).all()
        )
        assert len(runs) == 1
        outcomes = list(
            session.scalars(
                select(SourceElementOutcome).where(
                    SourceElementOutcome.source_element_id == elem.id, SourceElementOutcome.outcome == "quarantined"
                )
            ).all()
        )
        assert len(outcomes) == 1
        assert outcomes[0].extraction_run_id == runs[0].id
        assert "no active vision_ocr model configured" in (outcomes[0].reason or "")

        calls = []

        def fake_chat_completions(self, messages, **kwargs):
            calls.append((messages, kwargs))
            content = json.dumps(
                {
                    "region_id": elem.id,
                    "element_kind": "chart",
                    "transcription": "Revenue trend",
                    "factual_description": "A chart shows a revenue trend.",
                    "candidate_assertions": ["Revenue changes over time"],
                    "confidence": 0.91,
                }
            )
            return InferenceResponse(
                content=content,
                usage=InferenceUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
                raw_response={"choices": [{"message": {"content": content}}]},
            )

        monkeypatch.setattr(InferenceClient, "chat_completions", fake_chat_completions)
        _configure_vision_model(session)
        recovered = process_version_visual_elements(
            session, source_document=doc, source_version=ver, object_store=None, source_bytes=png_bytes
        )
        assert recovered[0].status == "success"
        assert len(calls) == 1

        task = session.scalar(select(ReviewTask).where(ReviewTask.target_id == elem.id))
        assert task is not None
        assert task.status == "closed"
        assert (
            session.scalar(
                select(SourceElementOutcome).where(
                    SourceElementOutcome.source_element_id == elem.id, SourceElementOutcome.outcome == "extracted"
                )
            )
            is not None
        )
        assert (
            len(
                session.scalars(select(UnmappedExtraction).where(UnmappedExtraction.source_element_id == elem.id)).all()
            )
            == 1
        )

        cached = process_version_visual_elements(
            session, source_document=doc, source_version=ver, object_store=None, source_bytes=png_bytes
        )
        assert cached[0].status == "success"
        assert len(calls) == 1
        assert (
            len(
                session.scalars(select(UnmappedExtraction).where(UnmappedExtraction.source_element_id == elem.id)).all()
            )
            == 1
        )


def test_ambiguous_vision_provider_quarantines_without_remote_call(session_factory, monkeypatch):
    called = []
    monkeypatch.setattr(InferenceClient, "chat_completions", lambda self, *args, **kwargs: called.append(True))

    with session_factory() as session:
        _configure_vision_model(session, model_id="vision-a")
        _configure_vision_model(session, model_id="vision-b")
        doc = SourceDocument(
            id="doc_vis_ambiguous",
            title="Ambiguous provider document",
            object_key="obj_ambiguous",
            media_type="image/png",
            sha256="sha_ambiguous",
            domain_id="OSS",
            category_id=TEST_CATEGORY_ID,
            sensitivity="internal",
        )
        ver = SourceDocumentVersion(
            id="ver_vis_ambiguous", source_document_id=doc.id, version_sha256="sha_ver_ambiguous"
        )
        elem = SourceElement(
            id="elem_ambiguous",
            source_version_id=ver.id,
            kind="diagram",
            ordinal=0,
            text="",
            asset_key="asset_ambiguous",
        )
        session.add_all((doc, ver, elem))
        session.flush()

        outcomes = process_version_visual_elements(
            session,
            source_document=doc,
            source_version=ver,
            object_store=None,
            source_bytes=(FIXTURES / "sample.png").read_bytes(),
        )

        assert called == []
        assert len(outcomes) == 1
        assert outcomes[0].status == "quarantined"
        assert outcomes[0].error_kind == "provider_rejection"
        assert "expected exactly one active configured vision_ocr model" in (outcomes[0].error_message or "")
        assert session.scalar(select(ReviewTask).where(ReviewTask.target_id == elem.id)) is not None


# =========================================================================
# 4. Invalid Capability & Policy Constraints
# =========================================================================


def test_invalid_capability_rejection_quarantines_element(session_factory, monkeypatch):
    called = []
    monkeypatch.setattr(InferenceClient, "chat_completions", lambda self, *args, **kwargs: called.append(True))

    with session_factory() as session:
        # Model does NOT support image input
        _configure_vision_model(session, model_id="no-image-model", capabilities={"image_input": False})

        doc = SourceDocument(
            id="doc_cap_test",
            title="Image Source",
            object_key="obj_cap",
            media_type="image/png",
            sha256="sha_cap",
            domain_id="OSS",
            category_id=TEST_CATEGORY_ID,
            sensitivity="internal",
        )
        session.add(doc)
        session.flush()

        ver = SourceDocumentVersion(id="ver_cap_test", source_document_id=doc.id, version_sha256="sha_ver_cap")
        session.add(ver)
        session.flush()

        elem = SourceElement(
            id="elem_diagram_cap", source_version_id=ver.id, kind="diagram", ordinal=0, text="", asset_key="asset_cap"
        )
        session.add(elem)
        session.flush()

        png_bytes = (FIXTURES / "sample.png").read_bytes()
        outcomes = process_version_visual_elements(
            session, source_document=doc, source_version=ver, object_store=None, source_bytes=png_bytes
        )

        # Rejection prior to remote call
        assert len(called) == 0
        assert len(outcomes) == 1
        assert outcomes[0].status == "quarantined"
        assert outcomes[0].error_kind == "provider_rejection"
        assert "does not support image input" in outcomes[0].error_message

        task = session.scalar(select(ReviewTask).where(ReviewTask.target_id == elem.id))
        assert task is not None
        assert "does not support image input" in task.reason


def test_malformed_capability_rejection_quarantines_element(session_factory, monkeypatch):
    called = []
    monkeypatch.setattr(InferenceClient, "chat_completions", lambda self, *args, **kwargs: called.append(True))

    with session_factory() as session:
        _configure_vision_model(
            session, model_id="malformed-model", capabilities={"max_request_bytes": "not-an-integer"}
        )
        doc = SourceDocument(
            id="doc_malformed_capability",
            title="Malformed capability document",
            object_key="obj_malformed_capability",
            media_type="image/png",
            sha256="sha_malformed_capability",
            domain_id="OSS",
            category_id=TEST_CATEGORY_ID,
            sensitivity="internal",
        )
        ver = SourceDocumentVersion(
            id="ver_malformed_capability", source_document_id=doc.id, version_sha256="sha_ver_malformed_capability"
        )
        elem = SourceElement(
            id="elem_malformed_capability",
            source_version_id=ver.id,
            kind="diagram",
            ordinal=0,
            text="",
            asset_key="asset_malformed_capability",
        )
        session.add_all((doc, ver, elem))
        session.flush()

        outcomes = process_version_visual_elements(
            session,
            source_document=doc,
            source_version=ver,
            object_store=None,
            source_bytes=(FIXTURES / "sample.png").read_bytes(),
        )

        assert called == []
        assert len(outcomes) == 1
        assert outcomes[0].status == "quarantined"
        assert "model capabilities are invalid" in (outcomes[0].error_message or "")


def test_unsupported_mime_type_rejection_quarantines_element(session_factory, monkeypatch):
    called = []
    monkeypatch.setattr(InferenceClient, "chat_completions", lambda self, *args, **kwargs: called.append(True))

    with session_factory() as session:
        # Model only supports TIFF
        _configure_vision_model(
            session,
            model_id="tiff-only-model",
            capabilities={"image_input": True, "supported_mime_types": ["image/tiff"]},
        )

        doc = SourceDocument(
            id="doc_mime_test",
            title="PNG Source",
            object_key="obj_mime",
            media_type="image/png",
            sha256="sha_mime",
            domain_id="OSS",
            category_id=TEST_CATEGORY_ID,
            sensitivity="internal",
        )
        session.add(doc)
        session.flush()

        ver = SourceDocumentVersion(id="ver_mime_test", source_document_id=doc.id, version_sha256="sha_ver_mime")
        session.add(ver)
        session.flush()

        elem = SourceElement(
            id="elem_mime", source_version_id=ver.id, kind="screenshot", ordinal=0, text="", asset_key="asset_mime"
        )
        session.add(elem)
        session.flush()

        png_bytes = (FIXTURES / "sample.png").read_bytes()
        outcomes = process_version_visual_elements(
            session, source_document=doc, source_version=ver, object_store=None, source_bytes=png_bytes
        )

        assert len(called) == 0
        assert len(outcomes) == 1
        assert outcomes[0].status == "quarantined"
        assert outcomes[0].error_kind == "unsupported_content"
        assert "not in model supported MIME types" in outcomes[0].error_message


def test_restricted_sensitivity_rejection_quarantines_element(session_factory, monkeypatch):
    called = []
    monkeypatch.setattr(InferenceClient, "chat_completions", lambda self, *args, **kwargs: called.append(True))

    with session_factory() as session:
        # Model only allowed for internal sensitivity
        _configure_vision_model(
            session,
            model_id="internal-only-model",
            capabilities={"image_input": True, "allowed_sensitivities": ["internal"]},
        )

        # Document is restricted
        doc = SourceDocument(
            id="doc_sens_test",
            title="Restricted Doc",
            object_key="obj_sens",
            media_type="image/png",
            sha256="sha_sens",
            domain_id="OSS",
            category_id=TEST_CATEGORY_ID,
            sensitivity="restricted",
        )
        session.add(doc)
        session.flush()

        ver = SourceDocumentVersion(id="ver_sens_test", source_document_id=doc.id, version_sha256="sha_ver_sens")
        session.add(ver)
        session.flush()

        elem = SourceElement(
            id="elem_sens", source_version_id=ver.id, kind="diagram", ordinal=0, text="", asset_key="asset_sens"
        )
        session.add(elem)
        session.flush()

        png_bytes = (FIXTURES / "sample.png").read_bytes()
        outcomes = process_version_visual_elements(
            session, source_document=doc, source_version=ver, object_store=None, source_bytes=png_bytes
        )

        assert len(called) == 0
        assert len(outcomes) == 1
        assert outcomes[0].status == "quarantined"
        assert "sensitivity 'restricted' is not in model allowed sensitivities" in outcomes[0].error_message


def test_restricted_domain_rejection_quarantines_element(session_factory, monkeypatch):
    called = []
    monkeypatch.setattr(InferenceClient, "chat_completions", lambda self, *args, **kwargs: called.append(True))

    with session_factory() as session:
        _configure_vision_model(
            session, model_id="corp-domain-model", capabilities={"image_input": True, "allowed_domains": ["BSS"]}
        )

        doc = SourceDocument(
            id="doc_dom_test",
            title="Engineering Doc",
            object_key="obj_dom",
            media_type="image/png",
            sha256="sha_dom",
            domain_id="OSS",
            category_id=TEST_CATEGORY_ID,
            sensitivity="internal",
        )
        session.add(doc)
        session.flush()

        ver = SourceDocumentVersion(id="ver_dom_test", source_document_id=doc.id, version_sha256="sha_ver_dom")
        session.add(ver)
        session.flush()

        elem = SourceElement(
            id="elem_dom", source_version_id=ver.id, kind="chart", ordinal=0, text="", asset_key="asset_dom"
        )
        session.add(elem)
        session.flush()

        png_bytes = (FIXTURES / "sample.png").read_bytes()
        outcomes = process_version_visual_elements(
            session, source_document=doc, source_version=ver, object_store=None, source_bytes=png_bytes
        )

        assert len(called) == 0
        assert len(outcomes) == 1
        assert outcomes[0].status == "quarantined"
        assert "domain 'OSS' is not in model allowed domains" in outcomes[0].error_message


def test_unhealthy_model_rejection_quarantines_element(session_factory, monkeypatch):
    called = []
    monkeypatch.setattr(InferenceClient, "chat_completions", lambda self, *args, **kwargs: called.append(True))

    with session_factory() as session:
        _configure_vision_model(
            session,
            model_id="unhealthy-model",
            last_test_status="failed",
            last_test_detail="endpoint timed out during probe",
        )

        doc = SourceDocument(
            id="doc_health_test",
            title="Doc",
            object_key="obj_health",
            media_type="image/png",
            sha256="sha_health",
            domain_id="OSS",
            category_id=TEST_CATEGORY_ID,
            sensitivity="internal",
        )
        session.add(doc)
        session.flush()

        ver = SourceDocumentVersion(id="ver_health_test", source_document_id=doc.id, version_sha256="sha_ver_health")
        session.add(ver)
        session.flush()

        elem = SourceElement(
            id="elem_health", source_version_id=ver.id, kind="diagram", ordinal=0, text="", asset_key="asset_health"
        )
        session.add(elem)
        session.flush()

        png_bytes = (FIXTURES / "sample.png").read_bytes()
        outcomes = process_version_visual_elements(
            session, source_document=doc, source_version=ver, object_store=None, source_bytes=png_bytes
        )

        assert len(called) == 0
        assert len(outcomes) == 1
        assert outcomes[0].status == "quarantined"
        assert "model health check is in failed state" in outcomes[0].error_message


# =========================================================================
# 5. Timeout & Invalid JSON Response Handling
# =========================================================================


def test_timeout_handling_quarantines_as_retryable(session_factory, monkeypatch):
    def fake_timeout(self, messages, **kwargs):
        raise InferenceTimeoutError("inference request timed out after 120s")

    monkeypatch.setattr(InferenceClient, "chat_completions", fake_timeout)

    with session_factory() as session:
        _configure_vision_model(session)

        doc = SourceDocument(
            id="doc_timeout_test",
            title="Timeout Doc",
            object_key="obj_timeout",
            media_type="image/png",
            sha256="sha_timeout",
            domain_id="OSS",
            category_id=TEST_CATEGORY_ID,
            sensitivity="internal",
        )
        session.add(doc)
        session.flush()

        ver = SourceDocumentVersion(id="ver_timeout_test", source_document_id=doc.id, version_sha256="sha_ver_timeout")
        session.add(ver)
        session.flush()

        elem = SourceElement(
            id="elem_timeout",
            source_version_id=ver.id,
            kind="difficult_scan",
            ordinal=0,
            text="original text",
            asset_key="asset_timeout",
        )
        session.add(elem)
        session.flush()

        png_bytes = (FIXTURES / "sample.png").read_bytes()
        outcomes = process_version_visual_elements(
            session, source_document=doc, source_version=ver, object_store=None, source_bytes=png_bytes
        )

        assert len(outcomes) == 1
        outcome = outcomes[0]
        assert outcome.status == "quarantined"
        assert outcome.is_retryable is True
        assert outcome.error_kind == "timeout"

        # Preserves original text, no placeholder
        assert elem.text == "original text"
        assert elem.metadata_["quarantined"] is True
        assert elem.metadata_["retryable"] is True
        assert elem.metadata_["error_kind"] == "timeout"

        task = session.scalar(select(ReviewTask).where(ReviewTask.target_id == elem.id))
        assert task is not None
        assert task.metadata_["retryable"] is True


def test_rate_limit_handling_quarantines_as_retryable(session_factory, monkeypatch):
    def fake_rate_limit(self, messages, **kwargs):
        raise InferenceUnavailableError("HTTP 429 Too Many Requests: rate limit exceeded")

    monkeypatch.setattr(InferenceClient, "chat_completions", fake_rate_limit)

    with session_factory() as session:
        _configure_vision_model(session)

        doc = SourceDocument(
            id="doc_rl_test",
            title="Rate Limit Doc",
            object_key="obj_rl",
            media_type="image/png",
            sha256="sha_rl",
            domain_id="OSS",
            category_id=TEST_CATEGORY_ID,
            sensitivity="internal",
        )
        session.add(doc)
        session.flush()

        ver = SourceDocumentVersion(id="ver_rl_test", source_document_id=doc.id, version_sha256="sha_ver_rl")
        session.add(ver)
        session.flush()

        elem = SourceElement(
            id="elem_rl", source_version_id=ver.id, kind="diagram", ordinal=0, text="", asset_key="asset_rl"
        )
        session.add(elem)
        session.flush()

        png_bytes = (FIXTURES / "sample.png").read_bytes()
        outcomes = process_version_visual_elements(
            session, source_document=doc, source_version=ver, object_store=None, source_bytes=png_bytes
        )

        assert len(outcomes) == 1
        assert outcomes[0].status == "quarantined"
        assert outcomes[0].is_retryable is True
        assert outcomes[0].error_kind == "rate_limit"


def test_invalid_json_response_handling_quarantines_as_terminal(session_factory, monkeypatch):
    def fake_invalid_json(self, messages, **kwargs):
        return InferenceResponse(
            content="I am an AI and here is the analysis: {not valid json",
            raw_response={
                "choices": [{"message": {"content": "I am an AI and here is the analysis: {not valid json"}}]
            },
        )

    monkeypatch.setattr(InferenceClient, "chat_completions", fake_invalid_json)

    with session_factory() as session:
        _configure_vision_model(session)

        doc = SourceDocument(
            id="doc_badjson_test",
            title="Bad JSON Doc",
            object_key="obj_badjson",
            media_type="image/png",
            sha256="sha_badjson",
            domain_id="OSS",
            category_id=TEST_CATEGORY_ID,
            sensitivity="internal",
        )
        session.add(doc)
        session.flush()

        ver = SourceDocumentVersion(id="ver_badjson_test", source_document_id=doc.id, version_sha256="sha_ver_badjson")
        session.add(ver)
        session.flush()

        elem = SourceElement(
            id="elem_badjson",
            source_version_id=ver.id,
            kind="screenshot",
            ordinal=0,
            text="original screenshot text",
            asset_key="asset_badjson",
        )
        session.add(elem)
        session.flush()

        png_bytes = (FIXTURES / "sample.png").read_bytes()
        outcomes = process_version_visual_elements(
            session, source_document=doc, source_version=ver, object_store=None, source_bytes=png_bytes
        )

        assert len(outcomes) == 1
        assert outcomes[0].status == "quarantined"
        assert outcomes[0].is_retryable is False
        assert outcomes[0].error_kind == "invalid_response"

        # Never substitute placeholder or invented text
        assert elem.text == "original screenshot text"
        assert elem.metadata_["quarantined"] is True

        task = session.scalar(select(ReviewTask).where(ReviewTask.target_id == elem.id))
        assert task is not None
        assert "invalid JSON" in task.reason
