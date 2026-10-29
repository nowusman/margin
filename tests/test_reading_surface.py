from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from margin_db.charter import save_charter
from margin_db.entity_models import Entity
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockRevision
from margin_db.narrative import compose_domain_narrative
from workers.extract.pipeline import _with_domain_charter
from workers.extract.windows import ExtractionWindow

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RANKING_PATH = REPOSITORY_ROOT / "packages" / "margin_retrieval" / "margin_retrieval" / "candidates.py"


def _window(window_id: str) -> ExtractionWindow:
    return ExtractionWindow(
        window_id=window_id,
        window_index=0,
        source_document_id="source-1",
        source_version_id="version-1",
        source_element_id="element-1",
        text="The payment API writes settled payments to the ledger.",
        content_type="text",
        strategy="text_overlap",
        source_page_id=None,
        page_number=None,
        section_heading=None,
        section_path="",
        preceding_context=(),
        following_context=(),
        context_element_ids=(),
        document_metadata={},
        estimated_tokens=10,
    )


def test_the_charter_reaches_extraction_only_within_its_own_domain(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        save_charter(
            session,
            domain_id="payments",
            author_principal_id="steward",
            purpose="Payment platform engineering.",
            scope_inclusions="",
            scope_exclusions="",
            terminology="",
            activate=True,
        )
        save_charter(
            session,
            domain_id="hiring",
            author_principal_id="steward",
            purpose="Hiring operations.",
            scope_inclusions="",
            scope_exclusions="",
            terminology="",
            activate=True,
        )
        session.commit()

        payments = _with_domain_charter(session, windows=(_window("window-payments"),), domain_id="payments")
        hiring = _with_domain_charter(session, windows=(_window("window-hiring"),), domain_id="hiring")
        unrelated = _with_domain_charter(session, windows=(_window("window-unrelated"),), domain_id="warehouse")

        assert "Payment platform engineering." in payments[0].domain_charter_context
        assert "Hiring operations." not in payments[0].domain_charter_context
        assert payments[0].domain_charter_version == 1
        assert "Hiring operations." in hiring[0].domain_charter_context
        assert unrelated[0].domain_charter_context == ""
        assert unrelated[0].domain_charter_version is None


def test_the_charter_is_never_a_ranking_input() -> None:
    """A charter is scope guidance. It may not manufacture relevance."""

    ranking_source = RANKING_PATH.read_text(encoding="utf-8").casefold()

    assert "charter" not in ranking_source
    assert "briefing" not in ranking_source


def test_the_briefing_is_supplied_as_context_and_never_as_evidence() -> None:
    from margin_retrieval.answering import _messages

    messages = _messages(
        system_prompt="",
        query="how does settlement work?",
        items=(),
        citations={},
        briefing="Domain purpose: Core platform engineering.",
    )
    payload = json.loads(messages[1]["content"])

    assert payload["domain_context"] == "Domain purpose: Core platform engineering."
    assert payload["evidence"] == []
    assert "not evidence" in payload["instructions"]
    assert "do not cite it" in payload["instructions"].casefold()


def test_a_narrative_distinguishes_verified_records_from_inferred_content(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        session.add(IdeaBlock(id="block-verified", domain_id="payments", current_revision_id="revision-verified"))
        session.add(
            IdeaBlockRevision(
                id="revision-verified",
                idea_block_id="block-verified",
                revision_number=1,
                statement="The payment API writes settled payments to the ledger.",
                normalized_statement="the payment api writes settled payments to the ledger",
                type="claim",
                confidence="verified",
                publication_status="verified",
                semantic_key="the-payment-api-writes-settled-payments-to-the-ledger",
            )
        )
        session.add(IdeaBlock(id="block-candidate", domain_id="payments", current_revision_id="revision-candidate"))
        session.add(
            IdeaBlockRevision(
                id="revision-candidate",
                idea_block_id="block-candidate",
                revision_number=1,
                statement="Settlement reconciles the ledger nightly.",
                normalized_statement="settlement reconciles the ledger nightly",
                type="claim",
                confidence="extracted",
                publication_status="candidate",
                semantic_key="settlement-reconciles-the-ledger-nightly",
            )
        )
        session.commit()

        narrative = compose_domain_narrative(
            session,
            "payments",
            readable_entity_ids=frozenset(),
            readable_idea_block_ids=frozenset({"block-verified", "block-candidate"}),
            readable_source_element_ids=frozenset(),
            permitted_governance_record_types=frozenset(),
        )

    markdown = str(narrative["markdown"])
    assert "## Verified claims" in markdown
    assert "## Claims awaiting verification" in markdown
    assert "- The payment API writes settled payments to the ledger." in markdown
    assert "- Settlement reconciles the ledger nightly." in markdown
    assert narrative["claim_count"] == 1
    assert narrative["inferred_claim_count"] == 1


def test_a_narrative_states_the_corpus_with_its_evidence_and_marks_authored_material(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        save_charter(
            session,
            domain_id="payments",
            author_principal_id="steward",
            purpose="Payment platform engineering.",
            scope_inclusions="",
            scope_exclusions="",
            terminology="",
            activate=True,
        )
        session.add(IdeaBlock(id="block-1", domain_id="payments", current_revision_id="revision-1"))
        session.add(
            IdeaBlockRevision(
                id="revision-1",
                idea_block_id="block-1",
                revision_number=1,
                statement="The payment API writes settled payments to the ledger.",
                normalized_statement="the payment api writes settled payments to the ledger",
                type="claim",
                confidence="verified",
                publication_status="verified",
                semantic_key="the-payment-api-writes-settled-payments-to-the-ledger",
            )
        )
        session.add(
            ClaimEvidence(
                id="evidence-1",
                idea_block_id="block-1",
                idea_block_revision_id="revision-1",
                source_element_id="element-1",
                source_quote="The payment API writes settled payments to the ledger.",
                confidence="verified",
                extraction_method="test",
                status="active",
            )
        )
        session.add(
            Entity(
                id="entity-api",
                preferred_label="Payment API",
                normalized_label="payment api",
                ontology_type="service",
                domain_id="payments",
                lifecycle_status="active",
            )
        )
        session.commit()

        narrative = compose_domain_narrative(
            session,
            "payments",
            readable_entity_ids=frozenset({"entity-api"}),
            readable_idea_block_ids=frozenset({"block-1"}),
            readable_source_element_ids=frozenset(),
            permitted_governance_record_types=frozenset(),
        )

    markdown = str(narrative["markdown"])
    assert "## Verified claims" in markdown
    assert "- The payment API writes settled payments to the ledger." in markdown
    assert "*Charter (v1, authored):* Payment platform engineering." in markdown
    assert "Payment API" in markdown

    references = narrative["references"]
    reference_pairs = {(str(reference["kind"]), str(reference["id"])) for reference in references}
    assert ("idea_block", "block-1") in reference_pairs
    assert any(kind == "charter" for kind, _identifier in reference_pairs)
