"""The lifecycle of an agent run, its proposals and its questions.

A run is bounded twice over: by an iteration budget and a token budget. It can
be cancelled at any point, and the cancellation is recorded rather than
silently applied. Every step of the run leaves an event behind, so the run can
be read afterwards instead of being trusted.

A proposal is a change the agent wants to make. It is never applied by the
agent. A human approves it, and even then the outcome is deliberately modest:
a sandbox artefact stays a sandbox artefact, and a change to a canonical
record becomes a review task rather than a fact.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from margin_db.agent_models import (
    EVENT_KIND_ERROR,
    EVENT_KIND_FINISHED,
    EVENT_KIND_HUMAN_INPUT,
    EVENT_KIND_ITERATION,
    EVENT_KIND_PROPOSAL,
    EVENT_KIND_STARTED,
    HUMAN_INPUT_STATE_ANSWERED,
    HUMAN_INPUT_STATE_CANCELLED,
    HUMAN_INPUT_STATE_PENDING,
    PROPOSAL_STATE_APPLIED,
    PROPOSAL_STATE_APPROVED,
    PROPOSAL_STATE_PROPOSED,
    PROPOSAL_STATE_REJECTED,
    RUN_STATE_CANCELLED,
    RUN_STATE_COMPLETED,
    RUN_STATE_FAILED,
    RUN_STATE_PENDING,
    RUN_STATE_RUNNING,
    AgentHumanInput,
    AgentProposal,
    AgentRun,
    AgentRunEvent,
)
from margin_db.audit import write_audit_event
from margin_schema.ids import agent_event_id, agent_human_input_id, agent_proposal_id, agent_run_id

DEFAULT_ITERATION_BUDGET = 8
DEFAULT_TOKEN_BUDGET = 32_000
MAX_ITERATION_BUDGET = 32
MAX_TOKEN_BUDGET = 200_000
PROPOSAL_TARGET_SANDBOX_FILE = "sandbox_file"
PROPOSAL_TARGET_CANONICAL_RECORD = "canonical_record"
AGENT_PROPOSAL_TASK_TYPE = "agent_proposal_review"


class AgentBudgetExceeded(RuntimeError):
    """Raised when a run exhausts its iteration or token budget."""


@dataclass(frozen=True, slots=True)
class HumanInputField:
    """One field a human is asked to supply."""

    key: str
    label: str
    type: str = "text"
    options: tuple[str, ...] = ()
    default: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "type": self.type,
            "options": list(self.options),
            "default": self.default,
        }


def create_run(
    session: Session,
    *,
    principal_id: str,
    question: str,
    domain_id: str | None = None,
    iteration_budget: int = DEFAULT_ITERATION_BUDGET,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    write_granted: bool = False,
    metadata: dict[str, Any] | None = None,
) -> AgentRun:
    """Create a run and record its first event."""

    run = AgentRun(
        id=agent_run_id(
            {
                "principal_id": principal_id,
                "question": question,
                "domain_id": domain_id,
                "started_at": datetime.now(UTC).isoformat(),
            }
        ),
        principal_id=principal_id,
        domain_id=domain_id,
        question=question,
        state=RUN_STATE_PENDING,
        iteration_budget=max(1, min(MAX_ITERATION_BUDGET, iteration_budget)),
        token_budget=max(1, min(MAX_TOKEN_BUDGET, token_budget)),
        write_granted=write_granted,
        metadata_=dict(metadata or {}),
    )
    session.add(run)
    session.flush()
    record_event(session, run, EVENT_KIND_STARTED, {"question": question, "domain_id": domain_id})
    return run


def start_run(session: Session, run: AgentRun) -> AgentRun:
    """Mark a run as underway."""

    run.state = RUN_STATE_RUNNING
    run.started_at = datetime.now(UTC)
    session.flush()
    return run


def record_event(session: Session, run: AgentRun, kind: str, payload: dict[str, Any] | None = None) -> AgentRunEvent:
    """Append one event to a run's trail."""

    sequence = _next_sequence(session, run.id)
    event = AgentRunEvent(
        id=agent_event_id({"run_id": run.id, "sequence": sequence}),
        run_id=run.id,
        sequence=sequence,
        kind=kind,
        payload=dict(payload or {}),
    )
    session.add(event)
    session.flush()
    return event


def run_events(session: Session, run_id: str) -> tuple[AgentRunEvent, ...]:
    """Return a run's events in order."""

    return tuple(
        session.scalars(
            select(AgentRunEvent).where(AgentRunEvent.run_id == run_id).order_by(AgentRunEvent.sequence)
        ).all()
    )


def consume_iteration(session: Session, run: AgentRun) -> int:
    """Spend one iteration, refusing to go past the budget."""

    if run.iterations_used >= run.iteration_budget:
        raise AgentBudgetExceeded(f"the run reached its budget of {run.iteration_budget} iterations")
    run.iterations_used += 1
    record_event(session, run, EVENT_KIND_ITERATION, {"iteration": run.iterations_used})
    session.flush()
    return run.iterations_used


def consume_tokens(session: Session, run: AgentRun, tokens: int) -> int:
    """Spend tokens, refusing to go past the budget."""

    if run.tokens_used + tokens > run.token_budget:
        raise AgentBudgetExceeded(f"the run reached its budget of {run.token_budget} tokens")
    run.tokens_used += tokens
    session.flush()
    return run.tokens_used


def is_cancelled(session: Session, run_id: str) -> bool:
    """Whether the run has been cancelled."""

    run = session.get(AgentRun, run_id)
    return run is not None and run.state == RUN_STATE_CANCELLED


def cancel_run(session: Session, run: AgentRun, *, reason: str = "") -> AgentRun:
    """Cancel a run and record why."""

    run.state = RUN_STATE_CANCELLED
    run.cancelled_at = datetime.now(UTC)
    run.completed_at = run.cancelled_at
    record_event(session, run, EVENT_KIND_FINISHED, {"state": RUN_STATE_CANCELLED, "reason": reason})
    session.flush()
    return run


def complete_run(session: Session, run: AgentRun, *, answer: str) -> AgentRun:
    """Finish a run with its answer."""

    run.state = RUN_STATE_COMPLETED
    run.answer = answer
    run.completed_at = datetime.now(UTC)
    record_event(session, run, EVENT_KIND_FINISHED, {"state": RUN_STATE_COMPLETED})
    session.flush()
    return run


def fail_run(session: Session, run: AgentRun, *, error: str) -> AgentRun:
    """Finish a run that could not complete."""

    run.state = RUN_STATE_FAILED
    run.error = error[:4_000]
    run.completed_at = datetime.now(UTC)
    record_event(session, run, EVENT_KIND_ERROR, {"error": run.error})
    session.flush()
    return run


def create_proposal(
    session: Session,
    run: AgentRun,
    *,
    target_type: str,
    target_id: str,
    path: str,
    previous_content: str,
    proposed_content: str,
    summary: str,
) -> AgentProposal:
    """Record a proposed change and its diff."""

    proposal = AgentProposal(
        id=agent_proposal_id(
            {
                "run_id": run.id,
                "target_type": target_type,
                "target_id": target_id,
                "path": path,
                "sequence": len(run_proposals(session, run.id)),
            }
        ),
        run_id=run.id,
        principal_id=run.principal_id,
        target_type=target_type,
        target_id=target_id,
        path=path,
        previous_content=previous_content,
        proposed_content=proposed_content,
        diff=compute_diff(previous_content, proposed_content, path=path),
        summary=summary[:1_000],
        state=PROPOSAL_STATE_PROPOSED,
    )
    session.add(proposal)
    session.flush()
    record_event(
        session,
        run,
        EVENT_KIND_PROPOSAL,
        {"proposal_id": proposal.id, "path": proposal.path, "target_type": proposal.target_type},
    )
    return proposal


def run_proposals(session: Session, run_id: str) -> tuple[AgentProposal, ...]:
    """Return the proposals of a run, oldest first."""

    return tuple(
        session.scalars(
            select(AgentProposal)
            .where(AgentProposal.run_id == run_id)
            .order_by(AgentProposal.created_at, AgentProposal.id)
        ).all()
    )


def approve_proposal(
    session: Session, proposal: AgentProposal, *, reviewed_by: str, sandbox_root: Path | None = None
) -> AgentProposal:
    """Approve a proposal and apply it in the way its target allows.

    A sandbox artefact is written into the sandbox. A change to a canonical
    record becomes a review task, because a proposal is a candidate and only
    the evidence path may make a record verified.
    """

    if proposal.state != PROPOSAL_STATE_PROPOSED:
        raise ValueError(f"the proposal is already {proposal.state}")
    proposal.state = PROPOSAL_STATE_APPROVED
    proposal.reviewed_by = reviewed_by
    proposal.reviewed_at = datetime.now(UTC)
    if proposal.target_type == PROPOSAL_TARGET_SANDBOX_FILE:
        if sandbox_root is None:
            raise ValueError("a sandbox root is required to apply a file proposal")
        root = (sandbox_root / proposal.run_id).resolve()
        candidate = Path(proposal.path)
        if candidate.is_absolute():
            raise ValueError("a proposal must name a path inside its run's sandbox")
        target = (root / candidate).resolve()
        if not target.is_relative_to(root):
            raise ValueError("a proposal must stay inside its run's sandbox")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(proposal.proposed_content, encoding="utf-8")
        proposal.state = PROPOSAL_STATE_APPLIED
    elif proposal.target_type == PROPOSAL_TARGET_CANONICAL_RECORD:
        _raise_review_task(session, proposal)
    write_audit_event(
        session,
        actor=reviewed_by,
        action="agent_proposal_approved",
        target_type=proposal.target_type,
        target_id=proposal.target_id,
        after={"proposal_id": proposal.id, "path": proposal.path, "state": proposal.state},
    )
    session.flush()
    return proposal


def _raise_review_task(session: Session, proposal: AgentProposal) -> None:
    """Turn an approved canonical-record proposal into review work.

    Approval is not publication. A proposal that would change a canonical
    record becomes a review task, so the change travels the same evidence and
    review path as anything else that would alter the corpus.
    """

    from margin_db.review_models import ReviewTask

    task = ReviewTask(
        task_type=AGENT_PROPOSAL_TASK_TYPE,
        target_type=proposal.target_type,
        target_id=proposal.target_id,
        reason=proposal.summary or "an agent proposed a change to a canonical record",
        recommendation=proposal.proposed_content[:2_000],
        metadata_={"agent_run_id": proposal.run_id, "agent_proposal_id": proposal.id, "path": proposal.path},
    )
    session.add(task)
    session.flush()
    proposal.state = PROPOSAL_STATE_APPLIED


def reject_proposal(session: Session, proposal: AgentProposal, *, reviewed_by: str, reason: str = "") -> AgentProposal:
    """Refuse a proposal."""

    if proposal.state != PROPOSAL_STATE_PROPOSED:
        raise ValueError(f"the proposal is already {proposal.state}")
    proposal.state = PROPOSAL_STATE_REJECTED
    proposal.reviewed_by = reviewed_by
    proposal.reviewed_at = datetime.now(UTC)
    if reason:
        proposal.summary = f"{proposal.summary}\nRejected: {reason}"[:1_000]
    write_audit_event(
        session,
        actor=reviewed_by,
        action="agent_proposal_rejected",
        target_type=proposal.target_type,
        target_id=proposal.target_id,
        after={"proposal_id": proposal.id, "reason": reason},
    )
    session.flush()
    return proposal


def request_human_input(
    session: Session,
    run: AgentRun,
    *,
    request_key: str,
    title: str,
    description: str,
    fields: tuple[HumanInputField, ...],
) -> AgentHumanInput:
    """Ask a human for what the agent needs to continue."""

    request = AgentHumanInput(
        id=agent_human_input_id({"run_id": run.id, "request_key": request_key}),
        run_id=run.id,
        request_key=request_key,
        title=title[:500],
        description=description[:2_000],
        fields=[field.as_dict() for field in fields],
        state=HUMAN_INPUT_STATE_PENDING,
    )
    session.add(request)
    session.flush()
    record_event(session, run, EVENT_KIND_HUMAN_INPUT, {"request_id": request.id, "request_key": request_key})
    return request


def answer_human_input(
    session: Session, request: AgentHumanInput, *, response: dict[str, Any], answered_by: str
) -> AgentHumanInput:
    """Supply the answer a run was waiting for."""

    if request.state != HUMAN_INPUT_STATE_PENDING:
        raise ValueError(f"the request is already {request.state}")
    request.state = HUMAN_INPUT_STATE_ANSWERED
    request.response = dict(response)
    request.answered_by = answered_by
    request.answered_at = datetime.now(UTC)
    session.flush()
    return request


def cancel_human_input(session: Session, request: AgentHumanInput) -> AgentHumanInput:
    """Close a question that will not be answered."""

    request.state = HUMAN_INPUT_STATE_CANCELLED
    request.answered_at = datetime.now(UTC)
    session.flush()
    return request


def compute_diff(previous_content: str, proposed_content: str, *, path: str = "") -> str:
    """Return a unified diff between two versions of a text."""

    diff = difflib.unified_diff(
        previous_content.splitlines(keepends=True),
        proposed_content.splitlines(keepends=True),
        fromfile=f"a/{path}" if path else "a/current",
        tofile=f"b/{path}" if path else "b/proposed",
    )
    return "".join(diff)


def run_to_dict(run: AgentRun) -> dict[str, Any]:
    """Render a run for reporting."""

    return {
        "id": run.id,
        "principal_id": run.principal_id,
        "domain_id": run.domain_id,
        "question": run.question,
        "state": run.state,
        "iteration_budget": run.iteration_budget,
        "token_budget": run.token_budget,
        "iterations_used": run.iterations_used,
        "tokens_used": run.tokens_used,
        "write_granted": run.write_granted,
        "answer": run.answer,
        "error": run.error,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }


def proposal_to_dict(proposal: AgentProposal) -> dict[str, Any]:
    """Render a proposal for reporting."""

    return {
        "id": proposal.id,
        "run_id": proposal.run_id,
        "target_type": proposal.target_type,
        "target_id": proposal.target_id,
        "path": proposal.path,
        "summary": proposal.summary,
        "diff": proposal.diff,
        "state": proposal.state,
        "reviewed_by": proposal.reviewed_by,
        "reviewed_at": proposal.reviewed_at.isoformat() if proposal.reviewed_at else None,
        "created_at": proposal.created_at.isoformat() if proposal.created_at else None,
    }


def event_to_dict(event: AgentRunEvent) -> dict[str, Any]:
    """Render an event for reporting."""

    return {
        "id": event.id,
        "sequence": event.sequence,
        "kind": event.kind,
        "payload": event.payload,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


def _next_sequence(session: Session, run_id: str) -> int:
    """Return the next event sequence number for a run."""

    existing = session.scalar(
        select(AgentRunEvent.sequence)
        .where(AgentRunEvent.run_id == run_id)
        .order_by(AgentRunEvent.sequence.desc())
        .limit(1)
    )
    return int(existing or 0) + 1
