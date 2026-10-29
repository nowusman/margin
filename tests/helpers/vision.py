from pathlib import Path

from margin_db.model_catalog_models import ModelConfiguration

FIXTURES = Path("tests/fixtures/source_ingestion")


def _configure_vision_model(
    session,
    *,
    model_id: str = "test-vision-ocr",
    status: str = "active",
    capabilities: dict | None = None,
    last_test_status: str | None = "healthy",
    last_test_detail: str | None = "probe succeeded",
) -> ModelConfiguration:
    caps = {
        "text_input": True,
        "image_input": True,
        "structured_output": True,
        "supported_mime_types": ["image/png", "image/jpeg", "image/webp", "image/tiff"],
    }
    if capabilities is not None:
        caps.update(capabilities)

    model = ModelConfiguration(
        id=model_id,
        display_name="Test Vision Model",
        purpose="vision_ocr",
        runtime="openai-compatible",
        model_name="test-vision-model",
        endpoint_url="http://provider.local/v1",
        secret_reference="env:TEST_SECRET",
        capabilities=caps,
        status=status,
        last_test_status=last_test_status,
        last_test_detail=last_test_detail,
        max_tokens=4096,
    )
    session.add(model)
    session.flush()
    return model
