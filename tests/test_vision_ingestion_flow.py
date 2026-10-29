from decimal import Decimal

from sqlalchemy import select

from margin_db.model_catalog_models import ModelConfiguration
from margin_db.object_storage import FilesystemObjectStore
from margin_db.source_ingestion import SourceIngestionService
from margin_db.source_models import SourceElement
from margin_inference import InferenceClient, InferenceResponse, InferenceUsage
from tests.helpers.vision import FIXTURES, _configure_vision_model


def test_end_to_end_ingestion_service_routes_and_processes_vision(session_factory, tmp_path, monkeypatch):
    structured_json = (
        '{"region_id":"sample.png:elem:image_0","element_kind":"image",'
        '"transcription":"System Metrics Dashboard",'
        '"factual_description":"Screenshot of latency and throughput graphs for Margin API.",'
        '"candidate_assertions":["Margin API monitors latency and throughput"],"confidence":0.98}'
    )

    def fake_chat(self, messages, **kwargs):
        del messages
        if kwargs.get("event") == "document_text_extraction":
            return InferenceResponse(
                content="System Metrics Dashboard",
                raw_response={"choices": [{"message": {"content": "System Metrics Dashboard"}}]},
            )
        return InferenceResponse(
            content=structured_json,
            usage=InferenceUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
            raw_response={"choices": [{"message": {"content": structured_json}}]},
        )

    monkeypatch.setattr(InferenceClient, "chat_completions", fake_chat)

    with session_factory() as session:
        session.add(
            ModelConfiguration(
                id="primary-extraction",
                display_name="Primary extraction model",
                purpose="extraction",
                runtime="openai-compatible",
                model_name="test-extract-model",
                endpoint_url="http://provider.local/v1",
                secret_reference="env:TEST_SECRET",
                system_prompt="Extract document text.",
                status="active",
                max_tokens=4096,
            )
        )
        _configure_vision_model(session)

        service = SourceIngestionService(FilesystemObjectStore(tmp_path / "object_store"))
        upload_result = service.upload_source(
            session,
            filename="sample.png",
            media_type="image/png",
            data=(FIXTURES / "sample.png").read_bytes(),
            domain_id="OSS",
            category="Design Document",
            sensitivity="internal",
            auto_process=True,
        )

        doc = upload_result.source_document
        assert upload_result.ingestion_job.error_message is None, upload_result.ingestion_job.error_message
        assert doc.status == "extraction_ready"
        version = service.latest_version_for_source(session, doc.id)
        assert version is not None
        elements = list(
            session.scalars(select(SourceElement).where(SourceElement.source_version_id == version.id)).all()
        )
        assert len(elements) == 1
        element = elements[0]
        assert element.kind in {"image", "picture"}
        assert "System Metrics Dashboard" in (element.text or "")
        assert element.confidence == Decimal("0.98")
        assert element.metadata_["quarantined"] is False
        assert element.metadata_["is_derived_visual"] is True

        job = service.latest_job_for_source(session, doc.id)
        assert job is not None
        assert job.metadata_.get("quality_report", {}).get("visual_processing") == {
            "processed": 1,
            "success": 1,
            "quarantined": 0,
        }
