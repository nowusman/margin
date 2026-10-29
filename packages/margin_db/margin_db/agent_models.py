"""The governed agent runtime: runs, their events, their proposals and their
questions.

An agent run is a bounded, cancellable unit of work with a visible trail. The
run record says who asked, what was asked, what it was allowed to do and what
it did. Every event is kept, so a run can be read afterwards rather than
merely trusted.

The agent never changes the corpus by itself. Writes become proposals with a
computed diff, and a proposal becomes real only when a human approves it. The
approval is itself an auditable activity.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from margin_db.base import Base, JsonDict, JsonList, utc_now

RUN_STATE_PENDING = "pending"
RUN_STATE_RUNNING = "running"
RUN_STATE_COMPLETED = "completed"
RUN_STATE_FAILED = "failed"
RUN_STATE_CANCELLED = "cancelled"
RUN_STATES = (RUN_STATE_PENDING, RUN_STATE_RUNNING, RUN_STATE_COMPLETED, RUN_STATE_FAILED, RUN_STATE_CANCELLED)

PROPOSAL_STATE_PROPOSED = "proposed"
PROPOSAL_STATE_APPROVED = "approved"
PROPOSAL_STATE_REJECTED = "rejected"
PROPOSAL_STATE_APPLIED = "applied"
PROPOSAL_STATES = (PROPOSAL_STATE_PROPOSED, PROPOSAL_STATE_APPROVED, PROPOSAL_STATE_REJECTED, PROPOSAL_STATE_APPLIED)

HUMAN_INPUT_STATE_PENDING = "pending"
HUMAN_INPUT_STATE_ANSWERED = "answered"
HUMAN_INPUT_STATE_CANCELLED = "cancelled"
HUMAN_INPUT_STATES = (HUMAN_INPUT_STATE_PENDING, HUMAN_INPUT_STATE_ANSWERED, HUMAN_INPUT_STATE_CANCELLED)

EVENT_KIND_STARTED = "started"
EVENT_KIND_ITERATION = "iteration"
EVENT_KIND_TOOL_CALL = "tool_call"
EVENT_KIND_TOOL_RESULT = "tool_result"
EVENT_KIND_REFERENCE = "reference"
EVENT_KIND_PROPOSAL = "proposal"
EVENT_KIND_HUMAN_INPUT = "human_input"
EVENT_KIND_ERROR = "error"
EVENT_KIND_FINISHED = "finished"
EVENT_KINDS = (
    EVENT_KIND_STARTED,
    EVENT_KIND_ITERATION,
    EVENT_KIND_TOOL_CALL,
    EVENT_KIND_TOOL_RESULT,
    EVENT_KIND_REFERENCE,
    EVENT_KIND_PROPOSAL,
    EVENT_KIND_HUMAN_INPUT,
    EVENT_KIND_ERROR,
    EVENT_KIND_FINISHED,
)


class AgentRun(Base):
    """One bounded, cancellable agent run."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_agent_runs_principal_id", "principal_id"),
        Index("ix_agent_runs_state", "state"),
        Index("ix_agent_runs_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    principal_id: Mapped[str] = mapped_column(Text, nullable=False)
    domain_id: Mapped[str | None] = mapped_column(Text)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, default=RUN_STATE_PENDING)
    iteration_budget: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    token_budget: Mapped[int] = mapped_column(Integer, nullable=False, default=32_000)
    iterations_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    write_granted: Mapped[bool] = mapped_column(default=False)
    answer: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[JsonDict] = mapped_column("metadata", JsonDict, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentRunEvent(Base):
    """One ordered event in an agent run's visible trail."""

    __tablename__ = "agent_run_events"
    __table_args__ = (Index("ix_agent_run_events_run_sequence", "run_id", "sequence"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str] = mapped_column(Text, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[JsonDict] = mapped_column(JsonDict, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AgentProposal(Base):
    """A proposed change awaiting a human decision."""

    __tablename__ = "agent_proposals"
    __table_args__ = (
        Index("ix_agent_proposals_run_id", "run_id"),
        Index("ix_agent_proposals_state", "state"),
        Index("ix_agent_proposals_principal_id", "principal_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str] = mapped_column(Text, nullable=False)
    principal_id: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    previous_content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    proposed_content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    diff: Mapped[str] = mapped_column(Text, nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    state: Mapped[str] = mapped_column(Text, nullable=False, default=PROPOSAL_STATE_PROPOSED)
    reviewed_by: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AgentHumanInput(Base):
    """A question the agent needs answered before it can continue."""

    __tablename__ = "agent_human_inputs"
    __table_args__ = (Index("ix_agent_human_inputs_run_id", "run_id"), Index("ix_agent_human_inputs_state", "state"))

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str] = mapped_column(Text, nullable=False)
    request_key: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    fields: Mapped[JsonList] = mapped_column(JsonList, nullable=False, default=list)
    state: Mapped[str] = mapped_column(Text, nullable=False, default=HUMAN_INPUT_STATE_PENDING)
    response: Mapped[JsonDict] = mapped_column(JsonDict, nullable=False, default=dict)
    answered_by: Mapped[str | None] = mapped_column(Text)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
