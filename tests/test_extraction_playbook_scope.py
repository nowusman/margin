from __future__ import annotations

import json
from pathlib import Path

import pytest

from margin_agent.playbooks import PLAYBOOK_PATH_ENVIRONMENT
from workers.extract.pipeline import _extraction_run_metadata, _with_domain_playbooks
from workers.extract.windows import ExtractionWindow


def _write_playbook(root: Path, *, domain: str, name: str, body: str) -> None:
    directory = root / domain / name
    directory.mkdir(parents=True)
    (directory / "playbook.json").write_text(
        json.dumps({"name": name, "description": f"How {domain} is read."}), encoding="utf-8"
    )
    (directory / "body.md").write_text(body, encoding="utf-8")


def _window(window_id: str) -> ExtractionWindow:
    return ExtractionWindow(
        window_id=window_id,
        window_index=0,
        source_document_id="source-1",
        source_version_id="version-1",
        source_element_id="element-1",
        text="The payment API writes settled payments to the ledger.",
        content_type="text",
        strategy="text_overlap",
        source_page_id=None,
        page_number=None,
        section_heading=None,
        section_path="",
        preceding_context=(),
        following_context=(),
        context_element_ids=(),
        document_metadata={},
        estimated_tokens=10,
    )


def test_a_domain_playbook_reaches_only_its_own_domain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_playbook(tmp_path, domain="payments", name="ledger", body="Prefer settlement records.")
    _write_playbook(tmp_path, domain="hiring", name="roles", body="Prefer job families.")
    monkeypatch.setenv(PLAYBOOK_PATH_ENVIRONMENT, str(tmp_path))

    payments = _with_domain_playbooks(windows=(_window("window-payments"),), domain_id="payments")
    hiring = _with_domain_playbooks(windows=(_window("window-hiring"),), domain_id="hiring")
    unrelated = _with_domain_playbooks(windows=(_window("window-unrelated"),), domain_id="warehouse")

    assert [playbook["name"] for playbook in payments[0].playbook_context] == ["ledger"]
    assert [playbook["name"] for playbook in hiring[0].playbook_context] == ["roles"]
    assert unrelated[0].playbook_context == ()


def test_the_influence_of_a_consulted_playbook_is_recorded_on_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_playbook(tmp_path, domain="payments", name="ledger", body="Prefer settlement records.")
    monkeypatch.setenv(PLAYBOOK_PATH_ENVIRONMENT, str(tmp_path))

    windows = _with_domain_playbooks(windows=(_window("window-1"),), domain_id="payments")
    metadata = _extraction_run_metadata(windows)

    assert metadata["playbooks"] == [{"name": "ledger", "version": "1"}]
