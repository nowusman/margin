from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker

from margin_db.user_models import Domain
from margin_retrieval.context_budget import (
    CONTEXT_BUDGET_ENVIRONMENT_VARIABLE,
    DEFAULT_CONTEXT_BUDGET_CHARS,
    configured_context_budget_chars,
    resolve_context_budget,
)
from margin_retrieval.interfaces import RetrievalScope
from margin_retrieval.service import RetrievalService


def test_the_budget_is_configured_globally_by_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CONTEXT_BUDGET_ENVIRONMENT_VARIABLE, raising=False)
    assert configured_context_budget_chars() == DEFAULT_CONTEXT_BUDGET_CHARS

    monkeypatch.setenv(CONTEXT_BUDGET_ENVIRONMENT_VARIABLE, "48000")
    assert configured_context_budget_chars() == 48_000
    assert resolve_context_budget(None).total_chars == 48_000

    monkeypatch.setenv(CONTEXT_BUDGET_ENVIRONMENT_VARIABLE, "not-a-number")
    assert configured_context_budget_chars() == DEFAULT_CONTEXT_BUDGET_CHARS


def test_an_explicit_budget_wins_over_the_global_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CONTEXT_BUDGET_ENVIRONMENT_VARIABLE, "48000")

    assert resolve_context_budget(16_000).total_chars == 16_000
    assert resolve_context_budget(16_000, override=8_000).total_chars == 8_000


def test_a_domain_may_configure_its_own_budget(session_factory: sessionmaker[Session]) -> None:
    service = RetrievalService()
    with session_factory() as session:
        session.add(Domain(id="commerce", name="Commerce", context_budget_chars=64_000))
        session.add(Domain(id="reference", name="Reference"))
        session.commit()

        assert service._domain_context_budget(session, RetrievalScope(domain_ids=("commerce",))) == 64_000
        assert service._domain_context_budget(session, RetrievalScope(domain_ids=("reference",))) is None
        assert service._domain_context_budget(session, RetrievalScope(domain_ids=("commerce", "reference"))) is None


def test_the_reported_breakdown_covers_the_whole_budget() -> None:
    budget = resolve_context_budget(32_000)

    assert budget.index_chars + budget.pages_chars + budget.response_reserve_chars == (budget.total_chars)
    assert budget.per_page_chars <= budget.pages_chars
    assert budget.as_dict() == {
        "total_chars": 32_000,
        "index_chars": 1_600,
        "pages_chars": 25_600,
        "response_reserve_chars": 4_800,
        "per_page_chars": 7_680,
    }
