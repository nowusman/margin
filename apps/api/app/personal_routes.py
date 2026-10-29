"""The personal layer for one reader: notes, saved records and conversations.

Everything here belongs to the requesting principal. Nothing in this module
reads or writes another reader's personal material, and a personal record can
never become a published claim.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.authz import permitted_governance_domains, permitted_governance_record_types
from app.catalog_helpers import can_read_entity, can_read_idea_block, can_read_source, require_any_read
from app.source_routes import get_session_factory
from margin_db.entity_models import Entity
from margin_db.governance_models import GovernanceRecord
from margin_db.knowledge_models import IdeaBlock, IdeaBlockEntity, IdeaBlockRevision
from margin_db.personal import (
    create_thread,
    delete_note,
    delete_saved_item,
    delete_thread,
    get_thread,
    list_notes,
    list_saved_items,
    list_threads,
    principal_affiliated_entity_ids,
    recent_views,
    record_view,
    resolve_target_label,
    resolve_target_provenance,
    save_item,
    save_note,
    thread_messages,
)
from margin_db.personal_models import (
    NOTE_VISIBILITIES,
    NOTE_VISIBILITY_PRIVATE,
    PERSONAL_TARGET_TYPES,
    TARGET_ENTITY,
    TARGET_GOVERNANCE_RECORD,
    TARGET_IDEA_BLOCK,
    TARGET_SOURCE_DOCUMENT,
)
from margin_db.source_models import SourceDocument
from margin_db.user_models import Domain

router = APIRouter(prefix="/api/personal", tags=["personal"])

FEED_LIMIT = 20
QUEUE_LIMIT = 15


class NoteRequest(BaseModel):
    target_type: str = Field(min_length=1, max_length=40)
    target_id: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=20_000)
    domain_id: str | None = Field(default=None, max_length=200)
    visibility: str = Field(default=NOTE_VISIBILITY_PRIVATE, max_length=20)


class SavedItemRequest(BaseModel):
    target_type: str = Field(min_length=1, max_length=40)
    target_id: str = Field(min_length=1, max_length=200)
    label: str | None = Field(default=None, max_length=500)
    tags: list[str] = Field(default_factory=list, max_length=20)
    domain_id: str | None = Field(default=None, max_length=200)


class ViewRequest(BaseModel):
    target_type: str = Field(min_length=1, max_length=40)
    target_id: str = Field(min_length=1, max_length=200)
    domain_id: str | None = Field(default=None, max_length=200)


class ThreadRequest(BaseModel):
    title: str = Field(default="New conversation", max_length=200)
    domain_id: str | None = Field(default=None, max_length=200)


def _principal(request: Request) -> str:
    principal_id = getattr(request.state, "principal_id", "")
    if not principal_id or principal_id == "anonymous":
        raise HTTPException(status_code=403, detail="a named principal is required for the personal layer")
    return principal_id


def _require_target(target_type: str) -> None:
    if target_type not in PERSONAL_TARGET_TYPES:
        raise HTTPException(status_code=422, detail="unsupported personal record type")


def _readable_target(session, request: Request, target_type: str, target_id: str) -> bool:
    """Whether the reader may read the record a personal item points at.

    A personal item references a canonical record. It never grants access to
    one, so a reader may only keep what they could already read.
    """

    if target_type == TARGET_IDEA_BLOCK:
        block = session.get(IdeaBlock, target_id)
        return block is not None and can_read_idea_block(session, request, block)
    if target_type == TARGET_ENTITY:
        entity = session.get(Entity, target_id)
        return entity is not None and can_read_entity(session, request, entity)
    if target_type == TARGET_SOURCE_DOCUMENT:
        source = session.get(SourceDocument, target_id)
        return source is not None and can_read_source(request, source, session)
    if target_type == TARGET_GOVERNANCE_RECORD:
        record = session.get(GovernanceRecord, target_id)
        if record is None:
            return False
        domains = permitted_governance_domains(request.state.permissions, "read")
        if domains is not None and record.domain_id not in domains:
            return False
        return record.record_type in permitted_governance_record_types(request.state.permissions, "read")
    return True


def _require_readable_target(session, request: Request, target_type: str, target_id: str) -> None:
    if not _readable_target(session, request, target_type, target_id):
        raise HTTPException(status_code=403, detail="a personal record may only reference a record you can read")


def _target_reference(
    session, request: Request, target_type: str, target_id: str
) -> tuple[tuple[str, str] | None, tuple[str, ...]]:
    """Resolve a personal target for display, but only if it may be read."""

    if not _readable_target(session, request, target_type, target_id):
        return None, ()
    return (
        resolve_target_label(session, target_type, target_id),
        resolve_target_provenance(session, target_type, target_id),
    )


def _note_payload(note, resolved: tuple[str, str] | None, provenance: tuple[str, ...] = ()) -> dict[str, object]:
    return {
        "id": note.id,
        "target_type": note.target_type,
        "target_id": note.target_id,
        "target_label": resolved[0] if resolved else note.target_id,
        "target_kind": resolved[1] if resolved else "",
        "provenance": list(provenance),
        "body": note.body,
        "visibility": note.visibility,
        "domain_id": note.domain_id,
        "created_at": note.created_at.isoformat() if note.created_at else None,
        "updated_at": note.updated_at.isoformat() if note.updated_at else None,
    }


def _saved_payload(item, resolved: tuple[str, str] | None, provenance: tuple[str, ...] = ()) -> dict[str, object]:
    return {
        "id": item.id,
        "target_type": item.target_type,
        "target_id": item.target_id,
        "target_label": resolved[0] if resolved else item.target_id,
        "target_kind": resolved[1] if resolved else "",
        "provenance": list(provenance),
        "label": item.label,
        "tags": list(item.tags or []),
        "domain_id": item.domain_id,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


@router.get("/notes")
def read_notes(request: Request) -> dict[str, object]:
    """Return the reader's own notes."""

    require_any_read(request)
    principal_id = _principal(request)
    with get_session_factory(request)() as session:
        notes = list_notes(session, principal_id)
        return {
            "notes": [
                _note_payload(note, *_target_reference(session, request, note.target_type, note.target_id))
                for note in notes
            ]
        }


@router.put("/notes")
def write_note(body: NoteRequest, request: Request) -> dict[str, object]:
    """Create or replace the reader's note against a record."""

    require_any_read(request)
    principal_id = _principal(request)
    _require_target(body.target_type)
    if body.visibility not in NOTE_VISIBILITIES:
        raise HTTPException(status_code=422, detail="unsupported note visibility")
    with get_session_factory(request)() as session:
        _require_readable_target(session, request, body.target_type, body.target_id)
        note = save_note(
            session,
            owner_principal_id=principal_id,
            target_type=body.target_type,
            target_id=body.target_id,
            body=body.body,
            domain_id=body.domain_id,
            visibility=body.visibility,
        )
        session.commit()
        return {"note": _note_payload(note, *_target_reference(session, request, note.target_type, note.target_id))}


@router.delete("/notes/{note_id}")
def remove_note(note_id: str, request: Request) -> dict[str, object]:
    """Remove one of the reader's notes."""

    require_any_read(request)
    principal_id = _principal(request)
    with get_session_factory(request)() as session:
        if not delete_note(session, owner_principal_id=principal_id, note_id=note_id):
            raise HTTPException(status_code=404, detail="note not found")
        session.commit()
        return {"status": "deleted", "note_id": note_id}


@router.get("/saved")
def read_saved(request: Request) -> dict[str, object]:
    """Return the records the reader has kept."""

    require_any_read(request)
    principal_id = _principal(request)
    with get_session_factory(request)() as session:
        items = list_saved_items(session, principal_id)
        return {
            "saved_items": [
                _saved_payload(item, *_target_reference(session, request, item.target_type, item.target_id))
                for item in items
            ]
        }


@router.put("/saved")
def write_saved(body: SavedItemRequest, request: Request) -> dict[str, object]:
    """Keep a record, or update how it is kept."""

    require_any_read(request)
    principal_id = _principal(request)
    _require_target(body.target_type)
    with get_session_factory(request)() as session:
        _require_readable_target(session, request, body.target_type, body.target_id)
        item = save_item(
            session,
            owner_principal_id=principal_id,
            target_type=body.target_type,
            target_id=body.target_id,
            label=body.label,
            tags=body.tags,
            domain_id=body.domain_id,
        )
        session.commit()
        return {
            "saved_item": _saved_payload(item, *_target_reference(session, request, item.target_type, item.target_id))
        }


@router.delete("/saved/{item_id}")
def remove_saved(item_id: str, request: Request) -> dict[str, object]:
    """Stop keeping a record."""

    require_any_read(request)
    principal_id = _principal(request)
    with get_session_factory(request)() as session:
        if not delete_saved_item(session, owner_principal_id=principal_id, item_id=item_id):
            raise HTTPException(status_code=404, detail="saved item not found")
        session.commit()
        return {"status": "deleted", "item_id": item_id}


@router.post("/views")
def write_view(body: ViewRequest, request: Request) -> dict[str, object]:
    """Record that the reader looked at a record."""

    require_any_read(request)
    principal_id = _principal(request)
    _require_target(body.target_type)
    with get_session_factory(request)() as session:
        _require_readable_target(session, request, body.target_type, body.target_id)
        view = record_view(
            session,
            owner_principal_id=principal_id,
            target_type=body.target_type,
            target_id=body.target_id,
            domain_id=body.domain_id,
        )
        session.commit()
        return {"status": "recorded", "target_id": view.target_id}


@router.get("/feed")
def read_feed(request: Request) -> dict[str, object]:
    """Return the reader's own feed and the reading queue behind it."""

    require_any_read(request)
    principal_id = _principal(request)
    permissions = request.state.permissions
    with get_session_factory(request)() as session:
        permitted_domains = sorted(
            domain.id
            for domain in session.scalars(select(Domain)).all()
            if permissions.allows_domain(domain.id, "read")
        )
        views = recent_views(session, principal_id, limit=FEED_LIMIT)
        items = list_saved_items(session, principal_id)
        notes = list_notes(session, principal_id)

        engaged = {view.target_id for view in views}
        engaged.update(item.target_id for item in items)
        engaged.update(note.target_id for note in notes)

        feed: list[dict[str, object]] = []
        for view in views:
            resolved, provenance = _target_reference(session, request, view.target_type, view.target_id)
            feed.append(
                {
                    "target_type": view.target_type,
                    "target_id": view.target_id,
                    "target_label": resolved[0] if resolved else view.target_id,
                    "target_kind": resolved[1] if resolved else "",
                    "provenance": list(provenance),
                    "reason": "you read this",
                    "viewed_at": view.viewed_at.isoformat() if view.viewed_at else None,
                }
            )

        affiliated_entity_ids = principal_affiliated_entity_ids(session, principal_id)
        if affiliated_entity_ids:
            team_rows = session.execute(
                select(IdeaBlockRevision.id, IdeaBlockRevision.statement, IdeaBlock.id)
                .join(IdeaBlock, IdeaBlock.id == IdeaBlockRevision.idea_block_id)
                .join(IdeaBlockEntity, IdeaBlockEntity.idea_block_id == IdeaBlock.id)
                .where(
                    IdeaBlockEntity.entity_id.in_(affiliated_entity_ids),
                    IdeaBlockRevision.publication_status == "verified",
                )
                .order_by(IdeaBlockRevision.id)
                .limit(FEED_LIMIT * 4)
            ).all()
            for revision_id, statement, idea_block_id in team_rows:
                if idea_block_id in engaged:
                    continue
                if not _readable_target(session, request, "idea_block", idea_block_id):
                    continue
                engaged.add(idea_block_id)
                feed.append(
                    {
                        "target_type": "idea_block",
                        "target_id": idea_block_id,
                        "target_label": statement or idea_block_id,
                        "target_kind": "Idea Block",
                        "provenance": list(resolve_target_provenance(session, "idea_block", idea_block_id)),
                        "reason": "your team works on this",
                        "revision_id": revision_id,
                    }
                )
                if len(feed) >= FEED_LIMIT:
                    break

        queue: list[dict[str, object]] = []
        if permitted_domains:
            rows = session.execute(
                select(IdeaBlockRevision.id, IdeaBlockRevision.statement, IdeaBlock.id)
                .join(
                    IdeaBlock,
                    (IdeaBlock.id == IdeaBlockRevision.idea_block_id)
                    & (IdeaBlock.current_revision_id == IdeaBlockRevision.id),
                )
                .where(IdeaBlock.domain_id.in_(permitted_domains), IdeaBlockRevision.publication_status == "verified")
                .order_by(IdeaBlockRevision.id)
                .limit(QUEUE_LIMIT * 4)
            ).all()
            for revision_id, statement, idea_block_id in rows:
                if idea_block_id in engaged:
                    continue
                if not _readable_target(session, request, "idea_block", idea_block_id):
                    continue
                engaged.add(idea_block_id)
                queue.append(
                    {
                        "target_type": "idea_block",
                        "target_id": idea_block_id,
                        "target_label": statement or idea_block_id,
                        "target_kind": "Idea Block",
                        "provenance": list(resolve_target_provenance(session, "idea_block", idea_block_id)),
                        "reason": "in a domain you read",
                        "revision_id": revision_id,
                    }
                )
                if len(queue) >= QUEUE_LIMIT:
                    break

    return {"feed": feed, "queue": queue, "permitted_domains": permitted_domains}


@router.get("/threads")
def read_threads(request: Request) -> dict[str, object]:
    """Return the reader's conversations."""

    require_any_read(request)
    principal_id = _principal(request)
    with get_session_factory(request)() as session:
        threads = list_threads(session, principal_id)
        return {
            "threads": [
                {
                    "id": thread.id,
                    "title": thread.title,
                    "domain_id": thread.domain_id,
                    "created_at": thread.created_at.isoformat() if thread.created_at else None,
                    "updated_at": thread.updated_at.isoformat() if thread.updated_at else None,
                }
                for thread in threads
            ]
        }


@router.post("/threads")
def write_thread(body: ThreadRequest, request: Request) -> dict[str, object]:
    """Start a new conversation."""

    require_any_read(request)
    principal_id = _principal(request)
    with get_session_factory(request)() as session:
        thread = create_thread(session, owner_principal_id=principal_id, title=body.title, domain_id=body.domain_id)
        session.commit()
        return {
            "thread": {
                "id": thread.id,
                "title": thread.title,
                "domain_id": thread.domain_id,
                "created_at": thread.created_at.isoformat() if thread.created_at else None,
                "updated_at": thread.updated_at.isoformat() if thread.updated_at else None,
            }
        }


@router.get("/threads/{thread_id}")
def read_thread(thread_id: str, request: Request) -> dict[str, object]:
    """Return one conversation with its turns."""

    require_any_read(request)
    principal_id = _principal(request)
    with get_session_factory(request)() as session:
        thread = get_thread(session, principal_id, thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail="thread not found")
        messages = thread_messages(session, thread.id)
        return {
            "thread": {
                "id": thread.id,
                "title": thread.title,
                "domain_id": thread.domain_id,
                "created_at": thread.created_at.isoformat() if thread.created_at else None,
                "updated_at": thread.updated_at.isoformat() if thread.updated_at else None,
            },
            "messages": [
                {
                    "id": message.id,
                    "role": message.role,
                    "content": message.content,
                    "citation_ids": list(message.citation_ids or []),
                    "position": message.position,
                    "created_at": message.created_at.isoformat() if message.created_at else None,
                }
                for message in messages
            ],
        }


@router.delete("/threads/{thread_id}")
def remove_thread(thread_id: str, request: Request) -> dict[str, object]:
    """Remove a conversation and its turns."""

    require_any_read(request)
    principal_id = _principal(request)
    with get_session_factory(request)() as session:
        if not delete_thread(session, owner_principal_id=principal_id, thread_id=thread_id):
            raise HTTPException(status_code=404, detail="thread not found")
        session.commit()
        return {"status": "deleted", "thread_id": thread_id}
