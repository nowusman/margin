from __future__ import annotations

from typing import Any

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from margin_db.source_ingestion import SourceIngestionService, SourceUploadResult
from margin_db.user_models import Domain
from margin_schema.ids import entity_id
from tests.helpers.catalog import catalog_entity

TEST_CATEGORY = "Design Document"
TEST_CATEGORY_ID = entity_id({"normalized_label": TEST_CATEGORY, "ontology_type": "category", "scope": "source_scope"})
TEST_DOMAIN_ID = "OSS"


def seed_source_scope(engine: Engine) -> None:
    with Session(engine) as session:
        session.add_all(
            [
                Domain(id="OSS", name="Operational Support Systems"),
                Domain(id="BSS", name="Business Support Systems"),
                *(
                    catalog_entity(
                        entity_id=(
                            TEST_CATEGORY_ID
                            if category == TEST_CATEGORY
                            else entity_id(
                                {"normalized_label": category, "ontology_type": "category", "scope": "source_scope"}
                            )
                        ),
                        preferred_label=category,
                        ontology_type="category",
                    )
                    for category in (TEST_CATEGORY, "Architecture Standards")
                ),
            ]
        )
        session.commit()


def upload_scoped_source(service: SourceIngestionService, session: Session, **values: Any) -> SourceUploadResult:
    values.setdefault("domain_id", TEST_DOMAIN_ID)
    values.setdefault("category", TEST_CATEGORY)
    return service.upload_source(session, **values)
