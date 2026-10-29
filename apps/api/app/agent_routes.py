"""HTTP operations for the governed agent.

A run is created, read, and cancelled. A proposal is approved or refused. A
question is answered. The agent itself never acts on the corpus directly: the
only way to change something is for a person to approve a proposal, and even
then the outcome is a sandbox artefact or a review task, never a fact.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.catalog_helpers import require_any_read
from app.source_routes import get_session_factory
from margin_agent.playbooks import Playbook, discover_playbooks, discovery_roots, select_playbooks
from margin_agent.runtime import (
    DEFAULT_ITERATION_BUDGET,
    DEFAULT_TOKEN_BUDGET,
    answer_human_input,
    approve_proposal,
    cancel_run,
    create_proposal,
    create_run,
    event_to_dict,
    proposal_to_dict,
    reject_proposal,
    run_events,
    run_proposals,
    run_to_dict,
)
from margin_db.agent_models import HUMAN_INPUT_STATE_PENDING, AgentHumanInput, AgentProposal, AgentRun

router = APIRouter(prefix="/api/agent", tags=["agent"])

SANDBOX_ENVIRONMENT = "MARGIN_AGENT_SANDBOX_ROOT"
DEFAULT_SANDBOX_DIRECTORY = ".margin-agent-sandbox"


class RunRequest(BaseModel):
    model_config = {"extra": "forbid"}

    question: str = Field(min_length=1, max_length=4_000)
    domain_id: str | None = Field(default=None, max_length=200)
    iteration_budget: int = Field(default=DEFAULT_ITERATION_BUDGET, ge=1, le=32)
    token_budget: int = Field(default=DEFAULT_TOKEN_BUDGET, ge=1, le=200_000)
    write_granted: bool = False


class ProposalRequest(BaseModel):
    model_config = {"extra": "forbid"}

    target_type: str = Field(default="sandbox_file", max_length=60)
    target_id: str = Field(min_length=1, max_length=200)
    path: str = Field(default="", max_length=400)
    previous_content: str = Field(default="", max_length=200_000)
    proposed_content: str = Field(default="", max_length=200_000)
    summary: str = Field(default="", max_length=1_000)


class HumanInputRequest(BaseModel):
    model_config = {"extra": "forbid"}

    response: dict[str, Any] = Field(default_factory=dict)


def _principal(request: Request) -> str:
    principal_id = getattr(request.state, "principal_id", "")
    if not principal_id or principal_id == "anonymous":
        raise HTTPException(status_code=403, detail="a named principal is required to use the agent")
    return principal_id


def _sandbox_root() -> Path:
    configured = os.environ.get(SANDBOX_ENVIRONMENT, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.cwd() / DEFAULT_SANDBOX_DIRECTORY).resolve()


def _load_run(session, run_id: str, principal_id: str) -> AgentRun:
    run = session.get(AgentRun, run_id)
    if run is None or run.principal_id != principal_id:
        raise HTTPException(status_code=404, detail="run not found")
    return run


def _may_write(request: Request, domain_id: str | None) -> bool:
    """Return whether the requester may run an agent that writes.

    A write grant is a permission the server grants, so a reader cannot grant
    it to themselves by asking for it.
    """

    permissions = request.state.permissions
    if permissions.allows_domain("*", "write"):
        return True
    return bool(domain_id) and permissions.allows_domain(domain_id, "write")


@router.post("/runs", status_code=201)
def create_agent_run(body: RunRequest, request: Request) -> dict[str, object]:
    """Start an agent run."""

    require_any_read(request)
    principal_id = _principal(request)
    if body.write_granted and not _may_write(request, body.domain_id):
        raise HTTPException(status_code=403, detail="missing write access for the agent run")
    with get_session_factory(request)() as session:
        run = create_run(
            session,
            principal_id=principal_id,
            question=body.question,
            domain_id=body.domain_id,
            iteration_budget=body.iteration_budget,
            token_budget=body.token_budget,
            write_granted=body.write_granted,
        )
        session.commit()
        return {"run": run_to_dict(run)}


@router.get("/runs")
def list_agent_runs(request: Request) -> dict[str, object]:
    """List the caller's runs."""

    require_any_read(request)
    principal_id = _principal(request)
    with get_session_factory(request)() as session:
        runs = session.scalars(
            select(AgentRun)
            .where(AgentRun.principal_id == principal_id)
            .order_by(AgentRun.created_at.desc(), AgentRun.id)
        ).all()
        return {"runs": [run_to_dict(run) for run in runs]}


@router.get("/runs/{run_id}")
def read_agent_run(run_id: str, request: Request) -> dict[str, object]:
    """Return a run with its trail, its proposals and its open questions."""

    require_any_read(request)
    principal_id = _principal(request)
    with get_session_factory(request)() as session:
        run = _load_run(session, run_id, principal_id)
        events = run_events(session, run.id)
        proposals = run_proposals(session, run.id)
        questions = session.scalars(
            select(AgentHumanInput)
            .where(AgentHumanInput.run_id == run.id)
            .order_by(AgentHumanInput.created_at, AgentHumanInput.id)
        ).all()
        return {
            "run": run_to_dict(run),
            "events": [event_to_dict(event) for event in events],
            "proposals": [proposal_to_dict(proposal) for proposal in proposals],
            "questions": [
                {
                    "id": question.id,
                    "request_key": question.request_key,
                    "title": question.title,
                    "description": question.description,
                    "fields": list(question.fields or []),
                    "state": question.state,
                    "response": dict(question.response or {}),
                }
                for question in questions
            ],
        }


@router.post("/runs/{run_id}/cancel")
def cancel_agent_run(run_id: str, request: Request) -> dict[str, object]:
    """Cancel a run."""

    require_any_read(request)
    principal_id = _principal(request)
    with get_session_factory(request)() as session:
        run = _load_run(session, run_id, principal_id)
        cancel_run(session, run, reason="cancelled by the reader")
        session.commit()
        return {"run": run_to_dict(run)}


@router.post("/runs/{run_id}/proposals", status_code=201)
def create_agent_proposal(run_id: str, body: ProposalRequest, request: Request) -> dict[str, object]:
    """Record a change the agent wants to make."""

    require_any_read(request)
    principal_id = _principal(request)
    with get_session_factory(request)() as session:
        run = _load_run(session, run_id, principal_id)
        proposal = create_proposal(
            session,
            run,
            target_type=body.target_type,
            target_id=body.target_id,
            path=body.path,
            previous_content=body.previous_content,
            proposed_content=body.proposed_content,
            summary=body.summary,
        )
        session.commit()
        return {"proposal": proposal_to_dict(proposal)}


@router.post("/proposals/{proposal_id}/approve")
def approve_agent_proposal(proposal_id: str, request: Request) -> dict[str, object]:
    """Approve a proposed change."""

    require_any_read(request)
    principal_id = _principal(request)
    with get_session_factory(request)() as session:
        proposal = session.get(AgentProposal, proposal_id)
        if proposal is None or proposal.principal_id != principal_id:
            raise HTTPException(status_code=404, detail="proposal not found")
        try:
            approve_proposal(session, proposal, reviewed_by=principal_id, sandbox_root=_sandbox_root())
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        session.commit()
        return {"proposal": proposal_to_dict(proposal)}


@router.post("/proposals/{proposal_id}/reject")
def reject_agent_proposal(proposal_id: str, request: Request) -> dict[str, object]:
    """Refuse a proposed change."""

    require_any_read(request)
    principal_id = _principal(request)
    with get_session_factory(request)() as session:
        proposal = session.get(AgentProposal, proposal_id)
        if proposal is None or proposal.principal_id != principal_id:
            raise HTTPException(status_code=404, detail="proposal not found")
        try:
            reject_proposal(session, proposal, reviewed_by=principal_id)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        session.commit()
        return {"proposal": proposal_to_dict(proposal)}


@router.post("/requests/{request_id}/answer")
def answer_agent_request(request_id: str, body: HumanInputRequest, request: Request) -> dict[str, object]:
    """Answer a question the agent asked."""

    require_any_read(request)
    principal_id = _principal(request)
    with get_session_factory(request)() as session:
        human_input = session.get(AgentHumanInput, request_id)
        if human_input is None:
            raise HTTPException(status_code=404, detail="request not found")
        run = session.get(AgentRun, human_input.run_id)
        if run is None or run.principal_id != principal_id:
            raise HTTPException(status_code=404, detail="request not found")
        if human_input.state != HUMAN_INPUT_STATE_PENDING:
            raise HTTPException(status_code=409, detail=f"the request is already {human_input.state}")
        answer_human_input(session, human_input, response=body.response, answered_by=principal_id)
        session.commit()
        return {
            "request": {"id": human_input.id, "state": human_input.state, "response": dict(human_input.response or {})}
        }


@router.get("/playbooks")
def list_agent_playbooks(request: Request) -> dict[str, object]:
    """List the playbooks that govern the domains this reader can read."""

    require_any_read(request)
    permissions = request.state.permissions
    domain_ids = sorted(domain_id for domain_id, actions in permissions.domain_permissions.items() if "read" in actions)
    playbooks = discover_playbooks(discovery_roots())
    selected: dict[tuple[str, str], Playbook] = {}
    for domain_id in domain_ids:
        for playbook in select_playbooks(playbooks, domain_id=domain_id):
            selected[(str(playbook.directory), playbook.name)] = playbook
    return {"playbooks": [playbook.summary() for playbook in selected.values()], "readable_domains": domain_ids}
