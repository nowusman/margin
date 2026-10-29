from __future__ import annotations

import re

import pytest

from margin_schema.ids import (
    ALLOWED_PREFIXES,
    agent_event_id,
    agent_human_input_id,
    agent_proposal_id,
    agent_run_id,
    assistant_message_id,
    assistant_thread_id,
    canonical_json,
    deterministic_id,
    domain_charter_id,
    element_id,
    entity_id,
    export_bundle_id,
    graph_decision_id,
    idea_block_id,
    page_id,
    personal_note_id,
    personal_view_id,
    relationship_id,
    saved_item_id,
    source_id,
    source_version_id,
)


def test_ids_are_stable_for_equivalent_json_payloads() -> None:
    left = {"name": "Order Service", "tags": ["oss", "orders"], "metadata": {"owner": "architecture", "criticality": 3}}
    right = {
        "metadata": {"criticality": 3, "owner": "architecture"},
        "tags": ["oss", "orders"],
        "name": "Order Service",
    }

    assert source_id(left) == source_id(right)
    assert source_id(left) == source_id(left)


def test_ids_use_requested_prefix_and_sha256_hex_digest() -> None:
    payload = {"source": "hld.pdf", "version": 1}

    generated = {
        source_id(payload),
        source_version_id(payload),
        page_id(payload),
        element_id(payload),
        idea_block_id(payload),
        entity_id(payload),
        relationship_id(payload),
        graph_decision_id(payload),
        domain_charter_id(payload),
        export_bundle_id(payload),
        agent_run_id(payload),
        agent_event_id(payload),
        agent_proposal_id(payload),
        agent_human_input_id(payload),
        personal_note_id(payload),
        saved_item_id(payload),
        personal_view_id(payload),
        assistant_thread_id(payload),
        assistant_message_id(payload),
    }

    assert len(generated) == len(ALLOWED_PREFIXES)
    for value in generated:
        assert re.fullmatch(
            r"(src_|srcv_|page_|elem_|ib_|ent_|rel_|gdec_|charter_|pnote_|saved_|pview_"
            r"|thread_|amsg_|export_|run_|runevt_|proposal_|runq_)[0-9a-f]{64}",
            value,
        )


def test_canonical_json_normalizes_dicts_sets_and_unicode() -> None:
    decomposed_e = "e\u0301"
    composed_e = "\u00e9"

    assert canonical_json({"b": {3, 1, 2}, "a": decomposed_e}) == canonical_json({"a": composed_e, "b": {2, 3, 1}})


def test_unknown_prefix_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown deterministic ID prefix"):
        deterministic_id("bad_", {"id": 1})


def test_entity_id_rejects_an_empty_normalized_label() -> None:
    with pytest.raises(ValueError, match="non-empty normalized label"):
        entity_id({"normalized_label": ""})
    with pytest.raises(ValueError, match="non-empty normalized label"):
        entity_id({"normalized_label": "--- !!!"})
