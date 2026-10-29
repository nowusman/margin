from __future__ import annotations

from pathlib import Path

MAX_CODE_LINES = 1200
CODE_SUFFIXES = {".py", ".ts", ".tsx"}
EXCLUDED_PARTS = {".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".venv", "__pycache__", "node_modules"}


def test_no_new_code_files_exceed_line_limit() -> None:
    oversized: list[tuple[int, Path]] = []
    for path in Path(".").rglob("*"):
        if not path.is_file() or path.suffix not in CODE_SUFFIXES:
            continue
        if any(part in EXCLUDED_PARTS for part in path.parts):
            continue
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        if line_count > MAX_CODE_LINES:
            oversized.append((line_count, path))

    assert oversized == []
