"""Domain playbooks: how a domain's material is read.

A playbook is authored guidance for a domain. It tells the extractor what the
domain's records mean, which kinds of record matter, and what a careful reader
should notice. It is configuration, not knowledge: it lives on disk, it is
versioned with the code that reads it, and it is never evidence.

The convention is a directory per playbook::

    playbooks/
      payments/
        ledger/
          playbook.json
          body.md
          references/
            settlement-rules.md

The manifest names and describes the playbook. The body is its guidance.
Supporting files are read only when something asks for them, so a playbook
costs nothing until it is needed.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MANIFEST_FILENAME = "playbook.json"
BODY_FILENAME = "body.md"
SUPPORTING_DIRECTORY = "references"
PROJECT_PLAYBOOK_DIRECTORY = "playbooks"
USER_PLAYBOOK_DIRECTORY = Path.home() / ".margin" / "playbooks"
PLAYBOOK_PATH_ENVIRONMENT = "MARGIN_PLAYBOOK_PATHS"

MAX_BODY_CHARS = 24_000
MAX_SUPPORTING_FILES = 20
MAX_SUPPORTING_FILE_CHARS = 12_000
MAX_DISCOVERY_DEPTH = 4


class PlaybookError(ValueError):
    """Raised when a playbook directory is malformed."""


@dataclass(frozen=True, slots=True)
class Playbook:
    """One discovered playbook."""

    name: str
    description: str
    version: str
    domain_id: str
    applies_to: tuple[str, ...]
    body: str
    directory: Path
    manifest_path: Path
    supporting_files: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def applies_to_domain(self, domain_id: str | None) -> bool:
        """Whether the playbook governs a domain."""

        if not self.domain_id:
            return True
        if not domain_id:
            return False
        return self.domain_id.casefold() == domain_id.casefold()

    def applies_to_kind(self, kind: str | None) -> bool:
        """Whether the playbook governs a kind of record.

        A caller that names no kind is asking for every playbook of the
        domain, so the declared kinds only narrow the choice when a kind is
        actually named.
        """

        if not self.applies_to or not kind:
            return True
        return kind.casefold() in {item.casefold() for item in self.applies_to}

    def prompt_section(self) -> str:
        """Render the playbook for injection into an extraction prompt."""

        if not self.body.strip():
            return ""
        return (
            f"Domain playbook '{self.name}' (version {self.version}) applies here. "
            f"It describes how this domain is read:\n{self.body.strip()}"
        )

    def summary(self) -> dict[str, Any]:
        """Return the playbook's identity for reporting."""

        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "domain_id": self.domain_id,
            "applies_to": list(self.applies_to),
            "path": str(self.manifest_path),
            "supporting_files": list(self.supporting_files),
        }


def discovery_roots(project_root: Path | None = None) -> tuple[Path, ...]:
    """Return the directories searched for playbooks, in precedence order."""

    roots: list[Path] = []
    configured = os.environ.get(PLAYBOOK_PATH_ENVIRONMENT, "")
    for entry in configured.split(os.pathsep):
        cleaned = entry.strip()
        if not cleaned:
            continue
        candidate = Path(cleaned).expanduser()
        if candidate.is_dir():
            roots.append(candidate)
    project = project_root or Path.cwd()
    project_directory = project / PROJECT_PLAYBOOK_DIRECTORY
    if project_directory.is_dir():
        roots.append(project_directory)
    if USER_PLAYBOOK_DIRECTORY.is_dir():
        roots.append(USER_PLAYBOOK_DIRECTORY)
    return tuple(roots)


def discover_playbooks(roots: tuple[Path, ...]) -> tuple[Playbook, ...]:
    """Find every readable playbook under the given roots."""

    found: list[Playbook] = []
    seen: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for manifest_path in sorted(root.rglob(MANIFEST_FILENAME)):
            if len(manifest_path.relative_to(root).parts) > MAX_DISCOVERY_DEPTH:
                continue
            if manifest_path in seen:
                continue
            seen.add(manifest_path)
            playbook = _load_playbook(manifest_path)
            if playbook is not None:
                found.append(playbook)
    return tuple(found)


def select_playbooks(
    playbooks: tuple[Playbook, ...], *, domain_id: str | None = None, kind: str | None = None
) -> tuple[Playbook, ...]:
    """Return the playbooks that govern a domain and a kind of record."""

    selected = [
        playbook for playbook in playbooks if playbook.applies_to_domain(domain_id) and playbook.applies_to_kind(kind)
    ]
    return tuple(sorted(selected, key=lambda playbook: (playbook.domain_id, playbook.name, playbook.version)))


def load_supporting_file(playbook: Playbook, relative_path: str) -> str:
    """Read one supporting file of a playbook, on demand."""

    candidate = (playbook.directory / SUPPORTING_DIRECTORY / relative_path).resolve()
    root = (playbook.directory / SUPPORTING_DIRECTORY).resolve()
    if root not in candidate.parents and candidate != root:
        raise PlaybookError("a supporting file must stay inside the playbook")
    if not candidate.is_file():
        raise PlaybookError(f"no such supporting file: {relative_path}")
    return candidate.read_text(encoding="utf-8")[:MAX_SUPPORTING_FILE_CHARS]


def _load_playbook(manifest_path: Path) -> Playbook | None:
    """Read one playbook manifest, its body and its supporting file names."""

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(manifest, dict):
        return None
    name = str(manifest.get("name") or "").strip()
    description = str(manifest.get("description") or "").strip()
    if not name or not description:
        return None
    directory = manifest_path.parent
    body_path = directory / BODY_FILENAME
    body = body_path.read_text(encoding="utf-8")[:MAX_BODY_CHARS] if body_path.is_file() else ""
    supporting_root = directory / SUPPORTING_DIRECTORY
    supporting_files: tuple[str, ...] = ()
    if supporting_root.is_dir():
        names = sorted(
            path.relative_to(supporting_root).as_posix() for path in supporting_root.rglob("*") if path.is_file()
        )
        supporting_files = tuple(names[:MAX_SUPPORTING_FILES])
    declared = manifest.get("domain")
    domain_id = declared.strip() if isinstance(declared, str) else directory.parent.name.strip()
    applies_to = manifest.get("applies_to")
    applies_to_values = (
        tuple(str(item).strip() for item in applies_to if str(item).strip()) if isinstance(applies_to, list) else ()
    )
    return Playbook(
        name=name,
        description=description,
        version=str(manifest.get("version") or "1"),
        domain_id=domain_id,
        applies_to=applies_to_values,
        body=body,
        directory=directory,
        manifest_path=manifest_path,
        supporting_files=supporting_files,
        metadata={
            key: value
            for key, value in manifest.items()
            if key not in {"name", "description", "version", "domain", "applies_to"}
        },
    )
