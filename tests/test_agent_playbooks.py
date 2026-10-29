from __future__ import annotations

import json
from pathlib import Path

import pytest

from margin_agent.playbooks import PlaybookError, discover_playbooks, load_supporting_file, select_playbooks


def _write_playbook(
    root: Path,
    *,
    domain: str,
    name: str,
    description: str = "How this domain is read.",
    applies_to: list[str] | None = None,
    body: str = "Prefer settlement records over payment intents.",
) -> Path:
    directory = root / domain / name
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {"name": name, "description": description, "version": "2", "applies_to": applies_to or []}
    (directory / "playbook.json").write_text(json.dumps(manifest), encoding="utf-8")
    (directory / "body.md").write_text(body, encoding="utf-8")
    references = directory / "references"
    references.mkdir(exist_ok=True)
    (references / "rules.md").write_text("Settlement rules apply.", encoding="utf-8")
    return directory


def test_a_playbook_is_discovered_with_its_manifest_and_body(tmp_path: Path) -> None:
    _write_playbook(tmp_path, domain="payments", name="ledger")

    playbooks = discover_playbooks((tmp_path,))

    assert len(playbooks) == 1
    playbook = playbooks[0]
    assert playbook.name == "ledger"
    assert playbook.description == "How this domain is read."
    assert playbook.version == "2"
    assert playbook.domain_id == "payments"
    assert "Prefer settlement records" in playbook.body
    assert playbook.supporting_files == ("rules.md",)


def test_discovery_is_ordered_and_deterministic(tmp_path: Path) -> None:
    _write_playbook(tmp_path, domain="payments", name="ledger")
    _write_playbook(tmp_path, domain="payments", name="accounts")
    _write_playbook(tmp_path, domain="hiring", name="roles")

    first = discover_playbooks((tmp_path,))
    second = discover_playbooks((tmp_path,))

    assert [playbook.manifest_path for playbook in first] == [playbook.manifest_path for playbook in second]
    assert [playbook.domain_id for playbook in first] == ["hiring", "payments", "payments"]


def test_a_playbook_is_selected_by_domain_and_by_kind(tmp_path: Path) -> None:
    _write_playbook(tmp_path, domain="payments", name="ledger", applies_to=["service"])
    _write_playbook(tmp_path, domain="payments", name="people", applies_to=["person"])
    _write_playbook(tmp_path, domain="hiring", name="roles")

    playbooks = discover_playbooks((tmp_path,))

    in_payments = select_playbooks(playbooks, domain_id="payments")
    assert {playbook.name for playbook in in_payments} == {"ledger", "people"}

    for_service = select_playbooks(playbooks, domain_id="payments", kind="service")
    assert {playbook.name for playbook in for_service} == {"ledger"}

    for_person = select_playbooks(playbooks, domain_id="payments", kind="person")
    assert {playbook.name for playbook in for_person} == {"people"}


def test_a_playbook_without_applicability_applies_everywhere(tmp_path: Path) -> None:
    _write_playbook(tmp_path, domain="payments", name="general", applies_to=[])
    manifest = tmp_path / "payments" / "general" / "playbook.json"
    manifest.write_text(
        json.dumps({"name": "general", "description": "How this domain is read.", "version": "2", "domain": ""}),
        encoding="utf-8",
    )

    playbooks = discover_playbooks((tmp_path,))
    assert playbooks[0].domain_id == ""

    assert len(select_playbooks(playbooks, domain_id="payments", kind="service")) == 1
    assert len(select_playbooks(playbooks, domain_id="hiring", kind="service")) == 1


def test_a_supporting_file_is_read_only_when_asked(tmp_path: Path) -> None:
    directory = _write_playbook(tmp_path, domain="payments", name="ledger")
    playbooks = discover_playbooks((tmp_path,))

    assert load_supporting_file(playbooks[0], "rules.md") == "Settlement rules apply."

    with pytest.raises(PlaybookError):
        load_supporting_file(playbooks[0], "../playbook.json")

    assert directory.exists()


def test_a_malformed_manifest_is_skipped(tmp_path: Path) -> None:
    broken = tmp_path / "payments" / "broken"
    broken.mkdir(parents=True)
    (broken / "playbook.json").write_text("{not json", encoding="utf-8")
    nameless = tmp_path / "payments" / "nameless"
    nameless.mkdir(parents=True)
    (nameless / "playbook.json").write_text(json.dumps({"description": "no name"}), encoding="utf-8")

    assert discover_playbooks((tmp_path,)) == ()


def test_the_prompt_section_carries_the_playbook_body(tmp_path: Path) -> None:
    _write_playbook(tmp_path, domain="payments", name="ledger")

    playbook = discover_playbooks((tmp_path,))[0]

    section = playbook.prompt_section()
    assert "Domain playbook 'ledger'" in section
    assert "Prefer settlement records" in section
