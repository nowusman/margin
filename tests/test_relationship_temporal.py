from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

from margin_db.relationship_evidence import _contradicted_relationships


def _relationship(identifier: str, relation_type: str, *, metadata: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=identifier,
        source_entity_id="entity-a",
        target_entity_id="entity-b",
        relation_type=relation_type,
        status="verified",
        valid_from=date(2026, 1, 1),
        valid_to=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        metadata_=metadata or {},
    )


def test_unqualified_contradiction_does_not_close_multiple_predicates() -> None:
    contradiction = _relationship("contradiction", "contradicts")
    rows = [_relationship("dependency", "depends_on"), _relationship("ownership", "belongs_to"), contradiction]

    assert _contradicted_relationships(rows, contradiction, contradiction_date=date(2026, 2, 1)) == []


def test_qualified_contradiction_only_targets_the_named_predicate() -> None:
    contradiction = _relationship("contradiction", "contradicts", metadata={"target_relation_type": "depends_on"})
    dependency = _relationship("dependency", "depends_on")
    rows = [dependency, _relationship("ownership", "belongs_to"), contradiction]

    assert _contradicted_relationships(rows, contradiction, contradiction_date=date(2026, 2, 1)) == [dependency]
