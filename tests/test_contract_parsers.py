from __future__ import annotations

from pathlib import Path

from margin_ingest.contracts import contract_output, parse_contract_file

FIXTURES = Path("tests/fixtures/source_ingestion")


def test_openapi_parser_creates_api_operations_and_schemas() -> None:
    parsed = parse_contract_file(FIXTURES / "service.openapi.yaml")

    assert parsed.contract_type == "openapi"
    assert parsed.entities[0]["kind"] == "API"
    assert parsed.entities[0]["name"] == "Customer Onboarding API"
    assert parsed.operations[0]["operation_id"] == "createCustomer"
    assert parsed.operations[0]["method"] == "POST"
    assert set(parsed.operations[0]["request_schemas"]) == {"CreateCustomerRequest"}
    assert {schema["name"] for schema in parsed.schemas} == {"CreateCustomerRequest", "Customer"}


def test_asyncapi_parser_creates_events_channels_producers_and_consumers() -> None:
    parsed = parse_contract_file(FIXTURES / "events.asyncapi.yaml")

    assert parsed.contract_type == "asyncapi"
    assert parsed.entities[0]["kind"] == "EventAPI"
    assert parsed.channels[0]["name"] == "customer.created"
    assert parsed.events[0]["name"] == "CustomerCreated"
    assert parsed.events[0]["payload_schema"] == "CustomerCreatedPayload"
    assert parsed.producers[0]["operation_id"] == "publishCustomerCreated"
    assert parsed.consumers[0]["operation_id"] == "consumeCustomerCreated"
    assert parsed.schemas[0]["name"] == "CustomerCreatedPayload"


def test_asyncapi_output_uses_event_targets_for_governed_relationships() -> None:
    source = FIXTURES / "events.asyncapi.yaml"
    parsed = parse_contract_file(source)

    output = contract_output(
        relative_path=source.name,
        parsed=parsed,
        source_element_id="elem_contract",
        text=source.read_text(encoding="utf-8"),
    )

    relationship_blocks = [block for block in output.idea_blocks if block.relationships]
    assert relationship_blocks
    assert all(block.entities[1].kind == "event" for block in relationship_blocks)
