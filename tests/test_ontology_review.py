from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from margin_db.entity_models import Entity
from margin_db.knowledge_models import IdeaBlockRevision
from margin_db.ontology_review import ONTOLOGY_UNSUPPORTED_RULE_ID, reevaluate_ontology_records
from margin_db.relationship_models import EntityRelationship
from margin_schema.ontology import ONTOLOGY_VERSION

SUPERSEDED_VERSION = "margin-ontology-superseded"


def test_reevaluation_restamps_records_the_current_ontology_still_supports(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        session.add(_entity("entity-api", "Order API", "api"))
        session.add(_entity("entity-ledger", "Ledger", "data_store"))
        session.add(
            EntityRelationship(
                id="relationship-api-ledger",
                source_entity_id="entity-api",
                target_entity_id="entity-ledger",
                relation_type="uses",
                confidence="verified",
                status="verified",
                ontology_version=SUPERSEDED_VERSION,
            )
        )
        session.add(_revision("revision-1"))
        session.commit()

        result = reevaluate_ontology_records(session)
        session.commit()

        assert result.restamped == 4
        assert result.withdrawn == 0
        assert result.findings == ()
        assert session.get(Entity, "entity-api").ontology_version == ONTOLOGY_VERSION
        assert session.get(EntityRelationship, "relationship-api-ledger").ontology_version == ONTOLOGY_VERSION
        assert session.get(IdeaBlockRevision, "revision-1").ontology_version == ONTOLOGY_VERSION


def test_reevaluation_withdraws_a_record_the_current_ontology_no_longer_supports(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        session.add(_entity("entity-unknown", "Unknown Thing", "not_a_kind"))
        session.commit()

        result = reevaluate_ontology_records(session)
        session.commit()

        assert result.withdrawn == 1
        assert result.restamped == 0
        assert [finding.rule_id for finding in result.findings] == [ONTOLOGY_UNSUPPORTED_RULE_ID]
        entity = session.get(Entity, "entity-unknown")
        assert entity.lifecycle_status == "retired"
        assert entity.retired_at is not None


def test_reevaluation_marks_a_relationship_the_current_ontology_no_longer_allows_as_stale(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        session.add(_entity("entity-person", "Ada Lovelace", "person"))
        session.add(_entity("entity-api", "Order API", "api"))
        session.add(
            EntityRelationship(
                id="relationship-person-api",
                source_entity_id="entity-person",
                target_entity_id="entity-api",
                relation_type="uses",
                confidence="verified",
                status="verified",
                ontology_version=SUPERSEDED_VERSION,
            )
        )
        session.commit()

        result = reevaluate_ontology_records(session)
        session.commit()

        assert result.withdrawn == 1
        assert [finding.rule_id for finding in result.findings] == [ONTOLOGY_UNSUPPORTED_RULE_ID]
        assert session.get(EntityRelationship, "relationship-person-api").status == "stale"


def test_reevaluation_leaves_records_written_under_the_current_version_alone(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        session.add(_entity("entity-api", "Order API", "api", ontology_version=ONTOLOGY_VERSION))
        session.commit()

        result = reevaluate_ontology_records(session)
        session.commit()

        assert result.restamped == 0
        assert result.withdrawn == 0
        assert result.findings == ()


def _entity(identifier: str, label: str, kind: str, *, ontology_version: str = SUPERSEDED_VERSION) -> Entity:
    return Entity(
        id=identifier,
        preferred_label=label,
        normalized_label=label.lower(),
        ontology_type=kind,
        domain_id="commerce",
        lifecycle_status="active",
        ontology_version=ontology_version,
    )


def _revision(identifier: str) -> IdeaBlockRevision:
    return IdeaBlockRevision(
        id=identifier,
        idea_block_id="idea-block-1",
        revision_number=1,
        statement="The order API uses the ledger.",
        normalized_statement="the order api uses the ledger",
        type="claim",
        confidence="verified",
        publication_status="verified",
        semantic_key="the-order-api-uses-the-ledger",
        ontology_version=SUPERSEDED_VERSION,
    )
