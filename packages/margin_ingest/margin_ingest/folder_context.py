"""Folder context for batch source ingestion.

When a body of material arrives as a tree of files, the directory it sits in
carries meaning: a file under `payments/ledger/` is about ledger work, and a
file under `hiring/` is not. Repeating that in the prompt as a categorisation
hint saves the reader from having to categorise every file by hand, and keeps
the decision visible rather than buried in a classification model.

The hint is guidance, never evidence. It tells the extractor where a document
came from; it never supports a claim.
"""

from __future__ import annotations

MAX_FOLDER_SEGMENTS = 6
MAX_CONTEXT_CHARS = 240
SEPARATOR = " > "


def folder_context_for_path(relative_path: str) -> str:
    """Return the folder path of a source file as a readable hint.

    ``payments/ledger/reconciliation.md`` becomes ``payments > ledger``. A file
    with no directory yields an empty context, which is the honest answer.
    """

    segments = _segments(relative_path)
    if len(segments) < 2:
        return ""
    folders = segments[:-1]
    if len(folders) > MAX_FOLDER_SEGMENTS:
        folders = folders[-MAX_FOLDER_SEGMENTS:]
    context = SEPARATOR.join(folders)
    if len(context) > MAX_CONTEXT_CHARS:
        context = context[-MAX_CONTEXT_CHARS:]
    return context


def categorisation_hint(folder_context: str) -> str:
    """Return the prompt line that tells the extractor how to use the folder."""

    if not folder_context:
        return ""
    return (
        f"The document sits under the folder '{folder_context}'. Use that to judge which kind of "
        "record the material describes. It is a placement hint, not evidence: never emit a claim "
        "on the strength of the folder name alone."
    )


def _segments(relative_path: str) -> list[str]:
    """Split a path into the segments that describe where the file sits."""

    normalised = relative_path.replace("\\", "/")
    segments: list[str] = []
    for segment in normalised.split("/"):
        cleaned = segment.strip()
        if not cleaned or cleaned in {".", ".."}:
            continue
        segments.append(cleaned)
    return segments
