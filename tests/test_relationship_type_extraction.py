from __future__ import annotations

from margin_schema.ontology import ENTITY_RELATIONSHIP_TYPES
from workers.extract.markdown import DeterministicMarkdownExtractor
from workers.extract.prompts import validate_prompt_output


def _item(**kwargs: object) -> object:
    item = type("Item", (), {})()
    for key, value in kwargs.items():
        setattr(item, key, value)
    return item


def test_deterministic_extraction_emits_only_entity_relationships() -> None:
    output = DeterministicMarkdownExtractor().extract(
        elements=[
            _item(
                id="elem_relationship_types",
                kind="paragraph",
                text_content="Checkout API depends on Inventory API. Gateway exposes Public API.",
            )
        ]
    )

    relationships = [relationship for block in output.idea_blocks for relationship in block.relationships]

    assert {relationship.relation_type for relationship in relationships} == {"depends_on", "exposes"}
    assert {relationship.relation_type for relationship in relationships} <= ENTITY_RELATIONSHIP_TYPES


def test_governance_and_source_document_pseudo_entities_are_quarantined() -> None:
    output = validate_prompt_output(
        """{
          "idea_blocks": [{
            "statement": "Customer Platform is documented by Platform Architecture.",
            "type": "relationship",
            "confidence": "extracted",
            "evidence": [{
              "source_element_id": "elem_1",
              "source_quote": "Customer Platform is documented by Platform Architecture."
            }],
            "entities": [
              {"name": "Customer Platform", "kind": "system", "role": "subject"},
              {"name": "Platform Architecture", "kind": "source_document", "role": "object"}
            ],
            "relationships": [{
              "source": "Customer Platform",
              "relation_type": "documented_by",
              "target": "Platform Architecture",
              "confidence": "extracted"
            }],
            "tags": [],
            "metadata": {}
          }]
        }"""
    )

    block = output.idea_blocks[0]
    assert {entity.kind for entity in block.entities} == {"system"}
    assert block.relationships == []
    assert {candidate.reason.value for candidate in block.unmapped} == {
        "invalid_semantic_fields",
        "references_unmapped_entity",
    }


def test_unknown_inference_relationship_is_quarantined_without_fabricating_an_edge() -> None:
    output = validate_prompt_output(
        """{
          "idea_blocks": [{
            "statement": "Order API synchronizes with CRM Platform.",
            "type": "relationship",
            "confidence": "extracted",
            "evidence": [{
              "source_element_id": "elem_1",
              "source_quote": "Order API synchronizes with CRM Platform."
            }],
            "entities": [
              {"name": "Order API", "kind": "api", "role": "subject"},
              {"name": "CRM Platform", "kind": "system", "role": "object"}
            ],
            "relationships": [{
              "source": "Order API",
              "relation_type": "synchronizes_with",
              "target": "CRM Platform",
              "confidence": "extracted"
            }],
            "tags": [],
            "metadata": {}
          }]
        }"""
    )

    block = output.idea_blocks[0]
    assert block.relationships == []
    assert block.unmapped[0].raw_payload["relation_type"] == "synchronizes_with"
