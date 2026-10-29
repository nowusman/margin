"""Reading, writing and ranking against the personal layer.

The personal layer is private by default. A note is a reader's own words
against a canonical record, a saved item is a record they have kept, a view is
a signal that they looked at something, and a thread is a conversation they
have had with Margin.

Personal signals never decide what is true. They only decide what a reader
sees first among records that already passed the same evidence and access
tests, and even then only within a narrow band around the strongest match.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from margin_db.entity_models import Entity
from margin_db.governance_models import GovernanceRecord
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockEntity, IdeaBlockRevision
from margin_db.personal_models import (
    MESSAGE_ROLE_ASSISTANT,
    MESSAGE_ROLE_USER,
    NOTE_VISIBILITY_PRIVATE,
    AssistantMessage,
    AssistantThread,
    PersonalNote,
    PersonalView,
    SavedItem,
)
from margin_db.relationship_models import EntityRelationship
from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement
from margin_db.user_models import User
from margin_schema.ids import (
    assistant_message_id,
    assistant_thread_id,
    personal_note_id,
    personal_view_id,
    saved_item_id,
)

RECENT_VIEW_WINDOW = timedelta(days=7)
SAVED_WEIGHT = 0.15
NOTED_WEIGHT = 0.10
RECENT_VIEW_WEIGHT = 0.05
DOMAIN_AFFINITY_WEIGHT = 0.05
FREQUENCY_WEIGHT = 0.02
MAX_FREQUENCY_WEIGHT = 0.08
TEAM_AFFINITY_WEIGHT = 0.05
MAX_TOTAL_BOOST = 0.5
TIE_BREAK_BAND = 0.8


@dataclass(frozen=True, slots=True)
class PersonalSignal:
    """Why one candidate was raised for one reader."""

    revision_id: str
    saved: bool
    noted: bool
    viewed_recently: bool
    domain_affinity: bool
    engagement_count: int = 0
    team_affinity: bool = False

    @property
    def weight(self) -> float:
        total = 0.0
        if self.saved:
            total += SAVED_WEIGHT
        if self.noted:
            total += NOTED_WEIGHT
        if self.viewed_recently:
            total += RECENT_VIEW_WEIGHT
        if self.domain_affinity:
            total += DOMAIN_AFFINITY_WEIGHT
        total += self.frequency_weight
        if self.team_affinity:
            total += TEAM_AFFINITY_WEIGHT
        return min(total, MAX_TOTAL_BOOST)

    @property
    def frequency_weight(self) -> float:
        """Reward a record the reader keeps returning to, within a bound."""

        if self.engagement_count <= 1:
            return 0.0
        return min(FREQUENCY_WEIGHT * (self.engagement_count - 1), MAX_FREQUENCY_WEIGHT)

    @property
    def reasons(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if self.saved:
            reasons.append("you saved this")
        if self.noted:
            reasons.append("you wrote about this")
        if self.viewed_recently:
            reasons.append("you read this recently")
        if self.engagement_count > 1:
            reasons.append(f"you have engaged with this {self.engagement_count} times")
        if self.domain_affinity:
            reasons.append("it belongs to a domain you read")
        if self.team_affinity:
            reasons.append("your team works on this")
        return tuple(reasons)


@dataclass(frozen=True, slots=True)
class PersonalBoost:
    """The result of applying personal signals, and what they changed."""

    candidates: tuple[tuple[str, float], ...]
    reordered: bool
    largest_position_shift: int
    largest_score_change: float


def resolve_target_label(session: Session, target_type: str, target_id: str) -> tuple[str, str] | None:
    """Return the display label and kind of a referenced record."""

    if target_type == "idea_block":
        block = session.get(IdeaBlock, target_id)
        if block is None:
            return None
        revision = session.get(IdeaBlockRevision, block.current_revision_id) if block.current_revision_id else None
        label = (revision.statement if revision is not None else None) or target_id
        return label, "Idea Block"
    if target_type == "entity":
        entity = session.get(Entity, target_id)
        if entity is None:
            return None
        return entity.preferred_label, entity.ontology_type
    if target_type == "governance_record":
        from margin_db.governance_models import GovernanceRecord

        record = session.get(GovernanceRecord, target_id)
        if record is None:
            return None
        return record.title, record.record_type
    if target_type == "source_document":
        from margin_db.source_models import SourceDocument

        source = session.get(SourceDocument, target_id)
        if source is None:
            return None
        return source.title, "Source Document"
    return None


def resolve_target_provenance(session: Session, target_type: str, target_id: str) -> tuple[str, ...]:
    """Return the source documents a referenced record was drawn from.

    A reader who keeps a record wants to know where it came from, so the
    personal surface shows the provenance of what it references rather than only
    its label.
    """

    if target_type == "idea_block":
        block = session.get(IdeaBlock, target_id)
        if block is None or not block.current_revision_id:
            return ()
        return _source_titles_for_revision(session, block.id, block.current_revision_id)
    if target_type == "entity":
        entity = session.get(Entity, target_id)
        if entity is None or not entity.definition_idea_block_id:
            return ()
        block = session.get(IdeaBlock, entity.definition_idea_block_id)
        if block is None or not block.current_revision_id:
            return ()
        return _source_titles_for_revision(session, block.id, block.current_revision_id)
    if target_type == "source_document":
        document = session.get(SourceDocument, target_id)
        return (document.title,) if document is not None else ()
    if target_type == "governance_record":
        record = session.get(GovernanceRecord, target_id)
        if record is None:
            return ()
        if not record.source_document_id:
            return ()
        document = session.get(SourceDocument, record.source_document_id)
        return (document.title,) if document is not None else ()
    return ()


def _source_titles_for_revision(session: Session, idea_block_id: str, revision_id: str) -> tuple[str, ...]:
    rows = session.execute(
        select(SourceDocument.title)
        .join(SourceDocumentVersion, SourceDocumentVersion.source_document_id == SourceDocument.id)
        .join(SourceElement, SourceElement.source_version_id == SourceDocumentVersion.id)
        .join(ClaimEvidence, ClaimEvidence.source_element_id == SourceElement.id)
        .where(
            ClaimEvidence.idea_block_id == idea_block_id,
            ClaimEvidence.idea_block_revision_id == revision_id,
            ClaimEvidence.status == "active",
            SourceDocumentVersion.status == "active",
        )
        .distinct()
        .order_by(SourceDocument.title)
    ).all()
    return tuple(row[0] for row in rows)


def list_notes(session: Session, owner_principal_id: str) -> tuple[PersonalNote, ...]:
    return tuple(
        session.scalars(
            select(PersonalNote)
            .where(PersonalNote.owner_principal_id == owner_principal_id)
            .order_by(PersonalNote.updated_at.desc(), PersonalNote.id)
        ).all()
    )


def save_note(
    session: Session,
    *,
    owner_principal_id: str,
    target_type: str,
    target_id: str,
    body: str,
    domain_id: str | None = None,
    visibility: str = NOTE_VISIBILITY_PRIVATE,
) -> PersonalNote:
    """Create or update the reader's note against a record."""

    existing = session.scalars(
        select(PersonalNote).where(
            PersonalNote.owner_principal_id == owner_principal_id,
            PersonalNote.target_type == target_type,
            PersonalNote.target_id == target_id,
        )
    ).first()
    if existing is not None:
        existing.body = body.strip()
        existing.visibility = visibility
        existing.domain_id = domain_id
        existing.updated_at = datetime.now(UTC)
        session.flush()
        return existing
    note = PersonalNote(
        id=personal_note_id(
            {"owner_principal_id": owner_principal_id, "target_type": target_type, "target_id": target_id}
        ),
        owner_principal_id=owner_principal_id,
        domain_id=domain_id,
        target_type=target_type,
        target_id=target_id,
        body=body.strip(),
        visibility=visibility,
    )
    session.add(note)
    session.flush()
    return note


def delete_note(session: Session, *, owner_principal_id: str, note_id: str) -> bool:
    note = session.get(PersonalNote, note_id)
    if note is None or note.owner_principal_id != owner_principal_id:
        return False
    session.delete(note)
    return True


def list_saved_items(session: Session, owner_principal_id: str) -> tuple[SavedItem, ...]:
    return tuple(
        session.scalars(
            select(SavedItem)
            .where(SavedItem.owner_principal_id == owner_principal_id)
            .order_by(SavedItem.created_at.desc(), SavedItem.id)
        ).all()
    )


def save_item(
    session: Session,
    *,
    owner_principal_id: str,
    target_type: str,
    target_id: str,
    label: str | None = None,
    tags: list[str] | None = None,
    domain_id: str | None = None,
) -> SavedItem:
    """Keep a record for the reader, or update the way it is kept."""

    existing = session.scalars(
        select(SavedItem).where(
            SavedItem.owner_principal_id == owner_principal_id,
            SavedItem.target_type == target_type,
            SavedItem.target_id == target_id,
        )
    ).first()
    if existing is not None:
        if label is not None:
            existing.label = label.strip() or None
        if tags is not None:
            existing.tags = [tag for tag in tags if tag]
        existing.domain_id = domain_id
        session.flush()
        return existing
    item = SavedItem(
        id=saved_item_id(
            {"owner_principal_id": owner_principal_id, "target_type": target_type, "target_id": target_id}
        ),
        owner_principal_id=owner_principal_id,
        domain_id=domain_id,
        target_type=target_type,
        target_id=target_id,
        label=(label or "").strip() or None,
        tags=[tag for tag in (tags or []) if tag],
    )
    session.add(item)
    session.flush()
    return item


def delete_saved_item(session: Session, *, owner_principal_id: str, item_id: str) -> bool:
    item = session.get(SavedItem, item_id)
    if item is None or item.owner_principal_id != owner_principal_id:
        return False
    session.delete(item)
    return True


def record_view(
    session: Session, *, owner_principal_id: str, target_type: str, target_id: str, domain_id: str | None = None
) -> PersonalView:
    """Record that the reader looked at a record, for recency signal."""

    view = PersonalView(
        id=personal_view_id(
            {
                "owner_principal_id": owner_principal_id,
                "target_type": target_type,
                "target_id": target_id,
                "viewed_at": datetime.now(UTC).isoformat(),
            }
        ),
        owner_principal_id=owner_principal_id,
        domain_id=domain_id,
        target_type=target_type,
        target_id=target_id,
        viewed_at=datetime.now(UTC),
    )
    session.add(view)
    session.flush()
    return view


def recent_views(session: Session, owner_principal_id: str, *, limit: int = 50) -> tuple[PersonalView, ...]:
    return tuple(
        session.scalars(
            select(PersonalView)
            .where(PersonalView.owner_principal_id == owner_principal_id)
            .order_by(PersonalView.viewed_at.desc(), PersonalView.id)
            .limit(limit)
        ).all()
    )


def list_threads(session: Session, owner_principal_id: str) -> tuple[AssistantThread, ...]:
    return tuple(
        session.scalars(
            select(AssistantThread)
            .where(AssistantThread.owner_principal_id == owner_principal_id)
            .order_by(AssistantThread.updated_at.desc(), AssistantThread.id)
        ).all()
    )


def get_thread(session: Session, owner_principal_id: str, thread_id: str) -> AssistantThread | None:
    thread = session.get(AssistantThread, thread_id)
    if thread is None or thread.owner_principal_id != owner_principal_id:
        return None
    return thread


def thread_messages(session: Session, thread_id: str) -> tuple[AssistantMessage, ...]:
    return tuple(
        session.scalars(
            select(AssistantMessage)
            .where(AssistantMessage.thread_id == thread_id)
            .order_by(AssistantMessage.position, AssistantMessage.created_at, AssistantMessage.id)
        ).all()
    )


def create_thread(
    session: Session, *, owner_principal_id: str, title: str, domain_id: str | None = None
) -> AssistantThread:
    """Start a conversation.

    The identity carries the moment the conversation was started, so two
    conversations with the same title are two conversations.
    """

    thread = AssistantThread(
        id=assistant_thread_id(
            {"owner_principal_id": owner_principal_id, "title": title, "started_at": datetime.now(UTC).isoformat()}
        ),
        owner_principal_id=owner_principal_id,
        domain_id=domain_id,
        title=title.strip()[:200] or "New conversation",
    )
    session.add(thread)
    session.flush()
    return thread


def append_message(
    session: Session, *, thread: AssistantThread, role: str, content: str, citation_ids: list[str] | None = None
) -> AssistantMessage:
    """Append a turn to a conversation and return it."""

    position = len(thread_messages(session, thread.id))
    message = AssistantMessage(
        id=assistant_message_id({"thread_id": thread.id, "position": position, "role": role}),
        thread_id=thread.id,
        owner_principal_id=thread.owner_principal_id,
        role=role,
        content=content,
        citation_ids=list(citation_ids or []),
        position=position,
    )
    session.add(message)
    thread.updated_at = datetime.now(UTC)
    session.flush()
    return message


def record_exchange(
    session: Session, *, thread: AssistantThread, question: str, answer: str, citation_ids: list[str] | None = None
) -> tuple[AssistantMessage, AssistantMessage]:
    """Persist a question and its answer as two ordered turns."""

    user_message = append_message(session, thread=thread, role=MESSAGE_ROLE_USER, content=question)
    assistant_message = append_message(
        session, thread=thread, role=MESSAGE_ROLE_ASSISTANT, content=answer, citation_ids=citation_ids
    )
    return user_message, assistant_message


def delete_thread(session: Session, *, owner_principal_id: str, thread_id: str) -> bool:
    thread = session.get(AssistantThread, thread_id)
    if thread is None or thread.owner_principal_id != owner_principal_id:
        return False
    session.execute(delete(AssistantMessage).where(AssistantMessage.thread_id == thread_id))
    session.delete(thread)
    return True


def personal_signals(
    session: Session, *, owner_principal_id: str, revision_ids: tuple[str, ...]
) -> dict[str, PersonalSignal]:
    """Compute the personal signals that apply to a set of candidate revisions.

    The signals are attached to the Idea Block behind a revision, because a
    reader recognises a claim, not a revision of a claim.
    """

    if not revision_ids or not owner_principal_id:
        return {}
    revisions = session.execute(
        select(IdeaBlockRevision.id, IdeaBlockRevision.idea_block_id, IdeaBlock.domain_id)
        .join(IdeaBlock, IdeaBlock.id == IdeaBlockRevision.idea_block_id)
        .where(IdeaBlockRevision.id.in_(revision_ids))
    ).all()
    if not revisions:
        return {}
    block_ids = {row[1] for row in revisions}

    saved_blocks = {
        row[0]
        for row in session.execute(
            select(IdeaBlock.id)
            .join(SavedItem, SavedItem.target_id == IdeaBlock.id)
            .where(
                SavedItem.owner_principal_id == owner_principal_id,
                SavedItem.target_type == "idea_block",
                IdeaBlock.id.in_(block_ids),
            )
        ).all()
    }
    noted_blocks = {
        row[0]
        for row in session.execute(
            select(IdeaBlock.id)
            .join(PersonalNote, PersonalNote.target_id == IdeaBlock.id)
            .where(
                PersonalNote.owner_principal_id == owner_principal_id,
                PersonalNote.target_type == "idea_block",
                IdeaBlock.id.in_(block_ids),
            )
        ).all()
    }
    viewed_blocks = {
        row[0]
        for row in session.execute(
            select(IdeaBlock.id)
            .join(PersonalView, PersonalView.target_id == IdeaBlock.id)
            .where(
                PersonalView.owner_principal_id == owner_principal_id,
                PersonalView.target_type == "idea_block",
                PersonalView.viewed_at >= datetime.now(UTC) - RECENT_VIEW_WINDOW,
                IdeaBlock.id.in_(block_ids),
            )
        ).all()
    }
    view_counts = {
        row[0]: row[1]
        for row in session.execute(
            select(PersonalView.target_id, func.count(PersonalView.id))
            .where(
                PersonalView.owner_principal_id == owner_principal_id,
                PersonalView.target_type == "idea_block",
                PersonalView.viewed_at >= datetime.now(UTC) - RECENT_VIEW_WINDOW,
                PersonalView.target_id.in_(block_ids),
            )
            .group_by(PersonalView.target_id)
        ).all()
    }

    affiliated_entity_ids = principal_affiliated_entity_ids(session, owner_principal_id)
    team_blocks: set[str] = set()
    if affiliated_entity_ids:
        team_blocks = {
            row[0]
            for row in session.execute(
                select(IdeaBlockEntity.idea_block_id).where(
                    IdeaBlockEntity.entity_id.in_(affiliated_entity_ids), IdeaBlockEntity.idea_block_id.in_(block_ids)
                )
            ).all()
        }

    engaged_domain_ids: frozenset[str] = frozenset()
    engaged_block_ids = saved_blocks | noted_blocks | viewed_blocks
    if engaged_block_ids:
        engaged_domain_ids = frozenset(
            row[0]
            for row in session.execute(select(IdeaBlock.domain_id).where(IdeaBlock.id.in_(engaged_block_ids))).all()
        )

    signals: dict[str, PersonalSignal] = {}
    for revision_id, idea_block_id, domain_id in revisions:
        engagement_count = (
            view_counts.get(idea_block_id, 0)
            + (1 if idea_block_id in saved_blocks else 0)
            + (1 if idea_block_id in noted_blocks else 0)
        )
        signal = PersonalSignal(
            revision_id=revision_id,
            saved=idea_block_id in saved_blocks,
            noted=idea_block_id in noted_blocks,
            viewed_recently=idea_block_id in viewed_blocks,
            domain_affinity=bool(domain_id) and domain_id in engaged_domain_ids,
            engagement_count=engagement_count,
            team_affinity=idea_block_id in team_blocks,
        )
        if signal.weight > 0:
            signals[revision_id] = signal
    return signals


def principal_team_entity_ids(session: Session, owner_principal_id: str) -> frozenset[str]:
    """Return the teams the principal belongs to.

    A principal is identified in the organisation catalogue by the external
    identifier carried on their person record. A principal who has no person
    record simply has no team, which is not an error.
    """

    identifiers = {owner_principal_id}
    user = session.get(User, owner_principal_id)
    if user is not None and user.email:
        identifiers.add(user.email)
    person_ids = {
        row[0]
        for row in session.execute(
            select(Entity.id).where(Entity.external_id.in_(identifiers), Entity.ontology_type == "person")
        ).all()
    }
    if not person_ids:
        return frozenset()
    return frozenset(
        row[0]
        for row in session.execute(
            select(EntityRelationship.target_entity_id).where(
                EntityRelationship.relation_type == "member_of", EntityRelationship.source_entity_id.in_(person_ids)
            )
        ).all()
    )


def principal_affiliated_entity_ids(session: Session, owner_principal_id: str) -> frozenset[str]:
    """Return the records the principal's team is responsible for.

    The team itself, the records the team owns, and the records the team works
    on are all records the reader has a stake in.
    """

    team_ids = principal_team_entity_ids(session, owner_principal_id)
    if not team_ids:
        return frozenset()
    owned = {
        row[0]
        for row in session.execute(
            select(EntityRelationship.source_entity_id).where(
                EntityRelationship.relation_type == "owned_by", EntityRelationship.target_entity_id.in_(team_ids)
            )
        ).all()
    }
    worked_on = {
        row[0]
        for row in session.execute(
            select(EntityRelationship.source_entity_id).where(
                EntityRelationship.relation_type == "works_on", EntityRelationship.target_entity_id.in_(team_ids)
            )
        ).all()
    }
    return frozenset(team_ids | owned | worked_on)


def apply_personal_boost(
    candidates: tuple[tuple[str, float], ...], signals: dict[str, PersonalSignal]
) -> PersonalBoost:
    """Reorder fused candidates using personal signals, within a narrow band.

    Only candidates close to the strongest match may be reordered, so a record
    the reader has saved can never overtake a substantially better match. The
    result records whether the ordering changed, and by how much, so that the
    effect of the personal layer is observable rather than invisible.
    """

    if not candidates or not signals:
        return PersonalBoost(candidates=candidates, reordered=False, largest_position_shift=0, largest_score_change=0.0)
    top_score = max(score for _, score in candidates)
    if top_score <= 0:
        return PersonalBoost(candidates=candidates, reordered=False, largest_position_shift=0, largest_score_change=0.0)
    floor = top_score * TIE_BREAK_BAND
    boosted: list[tuple[str, float]] = []
    largest_score_change = 0.0
    for revision_id, score in candidates:
        signal = signals.get(revision_id)
        if signal is None or score < floor:
            boosted.append((revision_id, score))
            continue
        boosted_score = score * (1.0 + signal.weight)
        largest_score_change = max(largest_score_change, boosted_score - score)
        boosted.append((revision_id, boosted_score))
    ordered = tuple(sorted(boosted, key=lambda item: (-item[1], item[0])))
    original_order = tuple(revision_id for revision_id, _ in candidates)
    boosted_order = tuple(revision_id for revision_id, _ in ordered)
    original_positions = {revision_id: index for index, revision_id in enumerate(original_order)}
    largest_position_shift = max(
        (abs(index - original_positions[revision_id]) for index, revision_id in enumerate(boosted_order)), default=0
    )
    return PersonalBoost(
        candidates=ordered,
        reordered=original_order != boosted_order,
        largest_position_shift=largest_position_shift,
        largest_score_change=largest_score_change,
    )
