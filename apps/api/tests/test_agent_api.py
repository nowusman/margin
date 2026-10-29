from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent_routes import router
from margin_agent.runtime import (
    AgentBudgetExceeded,
    HumanInputField,
    cancel_run,
    compute_diff,
    consume_iteration,
    consume_tokens,
    create_run,
    request_human_input,
    run_events,
)
from margin_auth.permissions import PermissionSnapshot
from margin_db.base import Base
from margin_db.user_models import Domain


def test_a_run_is_created_with_its_budgets_and_a_visible_trail() -> None:
    client = _client()

    created = client.post(
        "/api/agent/runs", json={"question": "Which records describe the payment flow?", "domain_id": "domain-a"}
    )

    assert created.status_code == 201
    run = created.json()["run"]
    assert run["state"] == "pending"
    assert run["question"] == "Which records describe the payment flow?"
    assert run["iteration_budget"] == 8
    assert run["write_granted"] is False

    read = client.get(f"/api/agent/runs/{run['id']}")
    assert read.status_code == 200
    assert read.json()["events"][0]["kind"] == "started"


def test_a_run_can_be_cancelled_and_the_cancellation_is_recorded() -> None:
    client = _client()

    created = client.post("/api/agent/runs", json={"question": "Something"})
    run_id = created.json()["run"]["id"]

    cancelled = client.post(f"/api/agent/runs/{run_id}/cancel")

    assert cancelled.status_code == 200
    assert cancelled.json()["run"]["state"] == "cancelled"
    events = client.get(f"/api/agent/runs/{run_id}").json()["events"]
    assert events[-1]["kind"] == "finished"
    assert events[-1]["payload"]["state"] == "cancelled"


def test_a_proposal_is_not_applied_until_a_person_approves_it() -> None:
    client = _client()
    sandbox = Path(os.environ["MARGIN_AGENT_SANDBOX_ROOT"])
    created = client.post("/api/agent/runs", json={"question": "Something"})
    run_id = created.json()["run"]["id"]

    proposed = client.post(
        f"/api/agent/runs/{run_id}/proposals",
        json={
            "target_type": "sandbox_file",
            "target_id": "note",
            "path": "notes/note.md",
            "previous_content": "old",
            "proposed_content": "new",
            "summary": "Reword the note",
        },
    )
    assert proposed.status_code == 201
    proposal = proposed.json()["proposal"]
    assert proposal["state"] == "proposed"
    assert "-old" in proposal["diff"]
    assert "+new" in proposal["diff"]
    assert not (sandbox / run_id / "notes" / "note.md").exists()

    approved = client.post(f"/api/agent/proposals/{proposal['id']}/approve")
    assert approved.status_code == 200
    assert approved.json()["proposal"]["state"] == "applied"
    assert (sandbox / run_id / "notes" / "note.md").read_text(encoding="utf-8") == "new"


def test_a_proposal_can_be_refused() -> None:
    client = _client()
    created = client.post("/api/agent/runs", json={"question": "Something"})
    run_id = created.json()["run"]["id"]
    proposed = client.post(
        f"/api/agent/runs/{run_id}/proposals", json={"target_id": "note", "path": "note.md", "proposed_content": "new"}
    )
    proposal = proposed.json()["proposal"]

    refused = client.post(f"/api/agent/proposals/{proposal['id']}/reject")

    assert refused.status_code == 200
    assert refused.json()["proposal"]["state"] == "rejected"


def test_a_question_is_answered_by_a_person() -> None:
    client = _client()
    created = client.post("/api/agent/runs", json={"question": "Something"})
    run_id = created.json()["run"]["id"]

    with _session_factory_for(client)() as session:
        from margin_db.agent_models import AgentRun

        run = session.get(AgentRun, run_id)
        assert run is not None
        request_human_input(
            session,
            run,
            request_key="owner",
            title="Which owner?",
            description="The record has more than one candidate owner.",
            fields=(HumanInputField(key="owner", label="Owner", type="select", options=("a", "b")),),
        )
        session.commit()

    read = client.get(f"/api/agent/runs/{run_id}")
    question = read.json()["questions"][0]
    assert question["state"] == "pending"
    assert question["fields"][0]["key"] == "owner"

    answered = client.post(f"/api/agent/requests/{question['id']}/answer", json={"response": {"owner": "a"}})

    assert answered.status_code == 200
    assert answered.json()["request"]["response"] == {"owner": "a"}


def test_a_run_refuses_to_exceed_its_budgets() -> None:
    with _session_factory()() as session:
        run = create_run(session, principal_id="reader", question="Something", iteration_budget=2, token_budget=100)
        consume_iteration(session, run)
        consume_iteration(session, run)
        with pytest.raises(AgentBudgetExceeded):
            consume_iteration(session, run)
        with pytest.raises(AgentBudgetExceeded):
            consume_tokens(session, run, 500)


def test_a_cancelled_run_is_reported_as_cancelled() -> None:
    with _session_factory()() as session:
        run = create_run(session, principal_id="reader", question="Something")
        cancel_run(session, run, reason="the reader changed their mind")
        assert run.state == "cancelled"
        assert any(event.kind == "finished" for event in run_events(session, run.id))


def test_the_diff_shows_what_changed() -> None:
    diff = compute_diff("one\ntwo\n", "one\nthree\n", path="notes/a.md")

    assert "-two" in diff
    assert "+three" in diff


@pytest.fixture(autouse=True)
def _sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the agent's sandbox at a temporary directory for each test."""

    sandbox = tmp_path / "sandbox"
    monkeypatch.setenv("MARGIN_AGENT_SANDBOX_ROOT", str(sandbox))
    return sandbox


def _session_factory() -> sessionmaker:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add(Domain(id="domain-a", name="Domain A"))
        session.commit()
    return factory


def _session_factory_for(client: TestClient) -> sessionmaker:
    """Return the session factory backing a test client."""

    app = client.app
    assert isinstance(app, FastAPI)
    return app.state.session_factory


def _client() -> TestClient:
    session_factory = _session_factory()
    app = FastAPI()

    @app.middleware("http")
    async def identity(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request.state.principal_id = "reader"
        request.state.permissions = PermissionSnapshot(
            principal_id="reader", domain_permissions={"domain-a": frozenset(("read",))}
        )
        return await call_next(request)

    app.state.session_factory = session_factory
    app.include_router(router)
    return TestClient(app)
