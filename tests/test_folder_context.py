from __future__ import annotations

from margin_ingest.folder_context import categorisation_hint, folder_context_for_path


def test_folder_context_reports_the_directory_a_file_sits_in() -> None:
    assert folder_context_for_path("payments/ledger/reconciliation.md") == "payments > ledger"
    assert folder_context_for_path("payments\\ledger\\reconciliation.md") == "payments > ledger"


def test_a_file_without_a_directory_has_no_folder_context() -> None:
    assert folder_context_for_path("reconciliation.md") == ""
    assert folder_context_for_path("") == ""


def test_folder_context_ignores_traversal_and_current_directory_segments() -> None:
    assert folder_context_for_path("./a/../b/notes.md") == "a > b"


def test_folder_context_keeps_the_deepest_folders_when_a_path_is_very_long() -> None:
    deep = "/".join(f"level{index}" for index in range(12))
    context = folder_context_for_path(f"{deep}/notes.md")
    assert context.count(" > ") == 5
    assert context.endswith(" > level11")


def test_the_categorisation_hint_says_the_folder_is_not_evidence() -> None:
    hint = categorisation_hint("payments > ledger")
    assert "payments > ledger" in hint
    assert "not evidence" in hint


def test_no_hint_is_produced_without_a_folder() -> None:
    assert categorisation_hint("") == ""
