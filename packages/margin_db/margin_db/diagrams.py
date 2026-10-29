from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement

DIAGRAM_MEDIA_TYPES = {
    "text/vnd.structurizr": "structurizr",
    "text/vnd.mermaid": "mermaid",
    "text/vnd.plantuml": "plantuml",
}

DIAGRAM_SUFFIX_TYPES = {
    ".dsl": "structurizr",
    ".mmd": "mermaid",
    ".mermaid": "mermaid",
    ".puml": "plantuml",
    ".plantuml": "plantuml",
    ".iuml": "plantuml",
}


@dataclass(frozen=True)
class DiagramRecord:
    id: str
    type: str
    title: str
    markup: str
    source_document: SourceDocument
    source_element_id: str | None

    def summary(self) -> dict[str, object]:
        return {"id": self.id, "type": self.type, "title": self.title, "evidence": [self.evidence()]}

    def detail(self) -> dict[str, object]:
        return {**self.summary(), "markup": self.markup, "svg": ""}

    def evidence(self) -> dict[str, object]:
        return {
            "source_document_id": self.source_document.id,
            "source_title": self.source_document.title,
            "source_uri": self.source_document.source_uri,
            "source_element_id": self.source_element_id,
        }


def list_diagram_records(session: Session) -> list[DiagramRecord]:
    documents = session.scalars(select(SourceDocument).order_by(SourceDocument.title, SourceDocument.id)).all()
    records: list[DiagramRecord] = []
    for document in documents:
        diagram_type = diagram_type_for_document(document)
        if diagram_type is None:
            continue
        markup, element_id = _diagram_markup(session, document.id)
        if not markup.strip():
            continue
        records.append(
            DiagramRecord(
                id=document.id,
                type=diagram_type,
                title=document.title,
                markup=markup,
                source_document=document,
                source_element_id=element_id,
            )
        )
    return records


def get_diagram_record(session: Session, diagram_id: str) -> DiagramRecord | None:
    document = session.get(SourceDocument, diagram_id)
    if document is None:
        return None
    diagram_type = diagram_type_for_document(document)
    if diagram_type is None:
        return None
    markup, element_id = _diagram_markup(session, document.id)
    if not markup.strip():
        return None
    return DiagramRecord(
        id=document.id,
        type=diagram_type,
        title=document.title,
        markup=markup,
        source_document=document,
        source_element_id=element_id,
    )


def diagram_type_for_document(document: SourceDocument) -> str | None:
    media_type = (document.media_type or "").strip().lower()
    if media_type in DIAGRAM_MEDIA_TYPES:
        return DIAGRAM_MEDIA_TYPES[media_type]
    source_uri = (document.source_uri or document.title or "").casefold()
    for suffix, diagram_type in DIAGRAM_SUFFIX_TYPES.items():
        if source_uri.endswith(suffix):
            return diagram_type
    return None


def _diagram_markup(session: Session, source_document_id: str) -> tuple[str, str | None]:
    version = session.scalar(
        select(SourceDocumentVersion)
        .where(SourceDocumentVersion.source_document_id == source_document_id, SourceDocumentVersion.status == "active")
        .order_by(SourceDocumentVersion.created_at.desc(), SourceDocumentVersion.id.desc())
        .limit(1)
    )
    if version is None:
        return "", None
    elements = session.scalars(
        select(SourceElement)
        .where(SourceElement.source_version_id == version.id)
        .order_by(SourceElement.ordinal, SourceElement.id)
    ).all()
    markup = "\n\n".join(elem.text or "" for elem in elements if elem.text)
    element_id = elements[0].id if elements else None
    return markup, element_id
