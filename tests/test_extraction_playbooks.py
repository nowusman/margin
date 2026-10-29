from __future__ import annotations

import json
from pathlib import Path

from workers.extract.inference import ExtractionRequest, _request_payload
from workers.extract.prompts import EXTRACTOR_SYSTEM_PROMPT


def test_the_charter_reaches_the_extraction_payload() -> None:
    payload = _request_payload(
        ExtractionRequest(
            window_id="window-1",
            source_element_id="element-1",
            text="The payment API writes settled payments to the ledger.",
            content_type="text",
            domain_charter_version=3,
            domain_charter_context="Domain purpose: Core platform engineering.",
        )
    )

    assert payload["domain_charter"] == {"version": 3, "context": "Domain purpose: Core platform engineering."}


def test_the_playbooks_reach_the_extraction_payload() -> None:
    payload = _request_payload(
        ExtractionRequest(
            window_id="window-1",
            source_element_id="element-1",
            text="The payment API writes settled payments to the ledger.",
            content_type="text",
            playbook_context=(
                {
                    "name": "ledger",
                    "version": "2",
                    "description": "How this domain is read.",
                    "guidance": "Prefer settlement records over payment intents.",
                },
            ),
        )
    )

    playbooks = payload["domain_playbooks"]
    assert isinstance(playbooks, tuple)
    assert playbooks[0]["name"] == "ledger"
    assert playbooks[0]["version"] == "2"


def test_the_extractor_prompt_treats_the_charter_and_playbooks_as_guidance() -> None:
    assert "placement hint, not evidence" in EXTRACTOR_SYSTEM_PROMPT
    assert "guidance, not evidence" in EXTRACTOR_SYSTEM_PROMPT


def test_a_playbook_body_is_rendered_for_injection(tmp_path: Path) -> None:
    from margin_agent.playbooks import discover_playbooks

    directory = tmp_path / "payments" / "ledger"
    directory.mkdir(parents=True)
    (directory / "playbook.json").write_text(
        json.dumps({"name": "ledger", "description": "How this domain is read."}), encoding="utf-8"
    )
    (directory / "body.md").write_text("Prefer settlement records.", encoding="utf-8")

    playbook = discover_playbooks((tmp_path,))[0]

    section = playbook.prompt_section()
    assert "Prefer settlement records." in section
    assert "version 1" in section
