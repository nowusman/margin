"""Evaluation of the surfaces built over the canonical record layer.

The record layer is measured against the gold corpus for extraction, evidence,
entity, relationship, governance, authorization, and retrieval behaviour. The
surfaces built on top of it are measured the same way, because a surface that
reports a record the corpus does not contain, or hides one it does, is as
defective as a claim without evidence.

Four properties are covered:

- **Graph traversal correctness**: a relationship the corpus records must be
  reachable as a graph edge, and only between records that are both present.
- **Narrative faithfulness**: a narrative must state the claims the corpus
  records, and must not state anything the corpus does not.
- **Export completeness**: an export must carry the records the corpus records
  and the reader may read, and nothing else.
- **Personal scoping**: one reader's personal material must be invisible to
  another, and a scoped read must not cross into a domain that was excluded.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from margin_auth.permissions import WILDCARD_SCOPE
from margin_db.entity_models import Entity
from margin_db.export import ExportScope, compose_domain_export
from margin_db.graph_subgraph import authorized_subgraph
from margin_db.knowledge_models import IdeaBlock, IdeaBlockRevision
from margin_db.narrative import compose_domain_narrative
from margin_db.personal import list_notes
from margin_eval.gold_corpus import GoldCorpus, GoldFixture, GoldRelationshipExpectation
from margin_eval.metrics import answer_relevance
from margin_schema.ontology import GOVERNANCE_RECORD_TYPES


@dataclass(frozen=True, slots=True)
class SurfaceQualityMetrics:
    """The measured properties of the surfaces over the gold corpus."""

    graph_edges_expected: int = 0
    graph_edges_found: int = 0
    narrative_claims_expected: int = 0
    narrative_claims_stated: int = 0
    narrative_stray_statements: int = 0
    export_records_expected: int = 0
    export_records_present: int = 0
    export_records_unexpected: int = 0
    personal_checks: int = 0
    personal_leaks: int = 0

    @property
    def graph_traversal_accuracy(self) -> float:
        return _ratio(self.graph_edges_found, self.graph_edges_expected)

    @property
    def narrative_faithfulness(self) -> float:
        return _ratio(self.narrative_claims_stated, self.narrative_claims_expected)

    @property
    def export_completeness(self) -> float:
        return _ratio(self.export_records_present, self.export_records_expected)

    @property
    def personal_isolation(self) -> float:
        return _ratio(self.personal_checks - self.personal_leaks, self.personal_checks)

    def as_dict(self) -> dict[str, int | float]:
        return {
            "graph_edges_expected": self.graph_edges_expected,
            "graph_edges_found": self.graph_edges_found,
            "graph_traversal_accuracy": round(self.graph_traversal_accuracy, 6),
            "narrative_claims_expected": self.narrative_claims_expected,
            "narrative_claims_stated": self.narrative_claims_stated,
            "narrative_stray_statements": self.narrative_stray_statements,
            "narrative_faithfulness": round(self.narrative_faithfulness, 6),
            "export_records_expected": self.export_records_expected,
            "export_records_present": self.export_records_present,
            "export_records_unexpected": self.export_records_unexpected,
            "export_completeness": round(self.export_completeness, 6),
            "personal_checks": self.personal_checks,
            "personal_leaks": self.personal_leaks,
            "personal_isolation": round(self.personal_isolation, 6),
        }


def evaluate_surface_quality(session: Session, corpus: GoldCorpus) -> SurfaceQualityMetrics:
    """Measure the surfaces over a corpus that has already been ingested."""

    graph_expected = 0
    graph_found = 0
    narrative_expected = 0
    narrative_stated = 0
    narrative_stray = 0
    export_expected = 0
    export_present = 0
    export_unexpected = 0
    personal_checks = 0
    personal_leaks = 0

    for fixture in corpus.fixtures:
        domain_id = fixture.domain_id
        entity_ids, idea_block_ids = _readable_scope(session, domain_id)

        graph_expected_here, graph_found_here = _graph_traversal(session, fixture, entity_ids, idea_block_ids)
        graph_expected += graph_expected_here
        graph_found += graph_found_here

        export_expected_here, export_present_here, export_unexpected_here = _export(
            session, fixture, entity_ids, idea_block_ids
        )
        export_expected += export_expected_here
        export_present += export_present_here
        export_unexpected += export_unexpected_here

    # A narrative is a view of a domain, so it is measured once per domain
    # rather than once per fixture.
    for domain_id in sorted({fixture.domain_id for fixture in corpus.fixtures}):
        entity_ids, idea_block_ids = _readable_scope(session, domain_id)
        narrative_expected_here, narrative_stated_here, narrative_stray_here = _narrative(
            session, domain_id, corpus, entity_ids, idea_block_ids
        )
        narrative_expected += narrative_expected_here
        narrative_stated += narrative_stated_here
        narrative_stray += narrative_stray_here

    personal_checks_here, personal_leaks_here = _personal_scoping(session, corpus)
    personal_checks += personal_checks_here
    personal_leaks += personal_leaks_here

    return SurfaceQualityMetrics(
        graph_edges_expected=graph_expected,
        graph_edges_found=graph_found,
        narrative_claims_expected=narrative_expected,
        narrative_claims_stated=narrative_stated,
        narrative_stray_statements=narrative_stray,
        export_records_expected=export_expected,
        export_records_present=export_present,
        export_records_unexpected=export_unexpected,
        personal_checks=personal_checks,
        personal_leaks=personal_leaks,
    )


def _readable_scope(session: Session, domain_id: str) -> tuple[frozenset[str], frozenset[str]]:
    """Return the entities and claims of a domain that a reader may read."""

    entity_ids = frozenset(
        entity_id
        for (entity_id,) in session.execute(
            select(Entity.id).where(Entity.domain_id == domain_id, Entity.lifecycle_status == "active")
        ).all()
    )
    idea_block_ids = frozenset(
        idea_block_id
        for (idea_block_id,) in session.execute(select(IdeaBlock.id).where(IdeaBlock.domain_id == domain_id)).all()
    )
    return entity_ids, idea_block_ids


def _graph_traversal(
    session: Session, fixture: GoldFixture, entity_ids: frozenset[str], idea_block_ids: frozenset[str]
) -> tuple[int, int]:
    """Check that every recorded relationship is reachable as a graph edge."""

    expected = tuple(relationship for relationship in fixture.relationships if _is_current_relationship(relationship))
    if not expected:
        return 0, 0
    entity_by_name = {
        entity.preferred_label: entity.id
        for entity in session.scalars(select(Entity).where(Entity.domain_id == fixture.domain_id)).all()
    }
    subgraph = authorized_subgraph(
        session,
        allowed_entity_ids=entity_ids,
        allowed_idea_block_ids=idea_block_ids,
        allowed_source_element_ids=_all_source_element_ids(session),
    )
    edges = {(edge.source_id, edge.target_id, edge.relation_type) for edge in subgraph.edges}
    found = 0
    for relationship in expected:
        source = entity_by_name.get(relationship.source_name)
        target = entity_by_name.get(relationship.target_name)
        if source is None or target is None:
            continue
        if (source, target, relationship.relation_type) in edges:
            found += 1
    return len(expected), found


def _is_current_relationship(relationship: GoldRelationshipExpectation) -> bool:
    """Whether a recorded relationship is a current edge of the graph.

    A refuted relationship and a relationship whose validity has ended are part
    of the record's history, not edges the graph draws. They are measured by
    the record layer instead.
    """

    if relationship.polarity != "supports":
        return False
    if relationship.valid_to is None:
        return True
    try:
        ended = date.fromisoformat(relationship.valid_to)
    except ValueError:
        return True
    return ended >= date.today()


def _narrative(
    session: Session, domain_id: str, corpus: GoldCorpus, entity_ids: frozenset[str], idea_block_ids: frozenset[str]
) -> tuple[int, int, int]:
    """Check that a narrative states the corpus's claims and nothing outside it.

    The gold corpus is the authority on what a narrative has to cover, so a
    corpus claim is covered when a narrative statement contains its content.
    The extracted corpus is the authority on what exists, so a narrative
    statement that the corpus does not hold is a stray.

    The narrative is composed for a reader who may read the corpus's sources,
    because a narrative quotes the evidence that reader may read and holds
    back the evidence they may not.
    """

    statements = _claim_statements(session, idea_block_ids)
    if not statements:
        return 0, 0, 0
    narrative = compose_domain_narrative(
        session,
        domain_id,
        readable_entity_ids=entity_ids,
        readable_idea_block_ids=idea_block_ids,
        readable_source_element_ids=_all_source_element_ids(session),
        permitted_governance_record_types=frozenset(),
    )
    markdown = str(narrative["markdown"])
    bullets = _narrative_statements(markdown)
    expected = tuple(
        claim.statement for fixture in corpus.fixtures if fixture.domain_id == domain_id for claim in fixture.claims
    )
    stated = sum(1 for claim in expected if any(answer_relevance(claim, bullet) >= 1.0 for bullet in bullets))
    stray = _stray_claim_statements(markdown, statements)
    return len(expected), stated, stray


def _narrative_statements(markdown: str) -> tuple[str, ...]:
    """Return the statements the narrative's claim sections make."""

    bullets: list[str] = []
    in_claims = False
    for line in markdown.splitlines():
        if line.startswith("## "):
            in_claims = line.strip().lower() in _CLAIM_SECTIONS
            continue
        if in_claims and line.startswith("- "):
            bullets.append(line[2:].strip())
    return tuple(bullets)


_CLAIM_SECTIONS = ("## verified claims", "## claims awaiting verification")


def _stray_claim_statements(markdown: str, statements: tuple[str, ...]) -> int:
    """Count claims the narrative states that the corpus does not contain.

    Every section that states claims is examined, whether it states verified
    records or inferred content. A narrative legitimately lists the records it
    covers; it may not state a claim the corpus does not hold.
    """

    stray = 0
    in_claims = False
    for line in markdown.splitlines():
        if line.startswith("## "):
            in_claims = line.strip().lower() in _CLAIM_SECTIONS
            continue
        if not in_claims or not line.startswith("- "):
            continue
        stripped = line[2:].strip()
        if stripped and not any(statement in stripped for statement in statements):
            stray += 1
    return stray


def _full_domain_scope(domain_id: str) -> ExportScope:
    """Return the scope of a reader who may read everything in a domain.

    The export scope records what the requester could read when they asked
    for it, and a grant that names no category names no readable category, so
    the scope of a reader with everything says so with the wildcard.
    """

    return ExportScope(
        domain_ids=frozenset({domain_id}),
        category_ids=frozenset({WILDCARD_SCOPE}),
        governance_record_types=frozenset(GOVERNANCE_RECORD_TYPES),
    )


def _export(
    session: Session, fixture: GoldFixture, entity_ids: frozenset[str], idea_block_ids: frozenset[str]
) -> tuple[int, int, int]:
    """Check that an export carries the readable records and nothing else."""

    statements = _claim_statements(session, idea_block_ids)
    entity_names = {
        entity.preferred_label
        for entity in session.scalars(select(Entity).where(Entity.domain_id == fixture.domain_id)).all()
    }
    if not statements and not entity_names:
        return 0, 0, 0
    scope = _full_domain_scope(fixture.domain_id)
    content = compose_domain_export(session, fixture.domain_id, scope)
    exported_entities = {str(row.get("preferred_label", "")) for row in content.records.get("entities", [])}
    exported_claims = {str(row.get("statement", "")) for row in content.records.get("idea_block_revisions", [])}
    expected = len(statements) + len(entity_names)
    present = sum(1 for statement in statements if statement in exported_claims) + sum(
        1 for name in entity_names if name in exported_entities
    )
    unexpected = len(exported_entities - entity_names)
    return expected, present, unexpected


def _personal_scoping(session: Session, corpus: GoldCorpus) -> tuple[int, int]:
    """Check that one reader's personal material is invisible to another."""

    from margin_db.personal import save_note

    checks = 0
    leaks = 0
    first_fixture = corpus.fixtures[0] if corpus.fixtures else None
    if first_fixture is None:
        return 0, 0
    target_id = f"surface-probe-{first_fixture.fixture_id}"
    save_note(
        session,
        owner_principal_id="surface-reader-a",
        target_type="idea_block",
        target_id=target_id,
        body="A private note that another reader must not see.",
    )
    session.flush()

    for reader in ("surface-reader-a", "surface-reader-b"):
        notes = list_notes(session, reader)
        checks += 1
        if reader == "surface-reader-b" and any(note.target_id == target_id for note in notes):
            leaks += 1
        if reader == "surface-reader-a" and not any(note.target_id == target_id for note in notes):
            leaks += 1

    other_domains = {fixture.domain_id for fixture in corpus.fixtures}
    scoped_domain = sorted(other_domains)[0] if other_domains else ""
    if scoped_domain:
        scope = _full_domain_scope(scoped_domain)
        excluded = [fixture for fixture in corpus.fixtures if fixture.domain_id != scoped_domain]
        excluded_ids = {
            entity.preferred_label
            for fixture in excluded
            for entity in session.scalars(select(Entity).where(Entity.domain_id == fixture.domain_id)).all()
        }
        content = compose_domain_export(session, scoped_domain, scope)
        exported = {str(row.get("preferred_label", "")) for row in content.records.get("entities", [])}
        checks += 1
        if exported & excluded_ids:
            leaks += 1
    return checks, leaks


def _claim_statements(session: Session, idea_block_ids: frozenset[str]) -> tuple[str, ...]:
    """Return the statements of the readable claims."""

    if not idea_block_ids:
        return ()
    rows = session.execute(
        select(IdeaBlockRevision.statement)
        .join(IdeaBlock, IdeaBlock.id == IdeaBlockRevision.idea_block_id)
        .where(IdeaBlockRevision.idea_block_id.in_(idea_block_ids))
        .order_by(IdeaBlockRevision.id)
    ).all()
    return tuple(str(row[0]) for row in rows if row[0])


def _all_source_element_ids(session: Session) -> frozenset[str]:
    """Return every source element identifier in the corpus."""

    from margin_db.source_models import SourceElement

    return frozenset(element_id for (element_id,) in session.execute(select(SourceElement.id)).all())


def _ratio(numerator: int, denominator: int) -> float:
    """Return the measured share.

    Nothing measured is not a pass. A property with no expectations has not
    been exercised, so it scores zero rather than a perfect result.
    """

    if denominator <= 0:
        return 0.0
    return numerator / denominator
