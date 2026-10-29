"""HTTP contracts for the unified Architecture Governance record API."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class GovernanceRecordCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_key: str = Field(min_length=1)
    record_type: str = Field(min_length=1)
    title: str = Field(min_length=1)
    domain_id: str = Field(min_length=1)
    attributes: dict[str, Any]
    owner_id: str | None = None
    steward_id: str | None = None
    effective_date: date | None = None
    review_date: date | None = None
    expiry_date: date | None = None
    target_date: date | None = None
    priority: str | None = None
    severity: int | None = Field(default=None, ge=0)
    external_issue_id: str | None = None
    external_issue_url: HttpUrl | None = None


class GovernanceRecordPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1)
    owner_id: str | None = None
    steward_id: str | None = None
    effective_date: date | None = None
    review_date: date | None = None
    expiry_date: date | None = None
    target_date: date | None = None
    priority: str | None = None
    severity: int | None = Field(default=None, ge=0)
    external_issue_id: str | None = None
    external_issue_url: HttpUrl | None = None
    attributes: dict[str, Any] | None = None
    reason: str = Field(min_length=1)
    expected_revision_id: str = Field(min_length=1)


class GovernanceTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_status: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    expected_revision_id: str = Field(min_length=1)
    attributes: dict[str, Any] | None = None


class WaiverRenewal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expiry_date: date
    reason: str = Field(min_length=1)
    expected_revision_id: str = Field(min_length=1)


class GovernanceLifecycleOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1)
    expected_revision_id: str = Field(min_length=1)


class TechnicalDebtResolution(GovernanceLifecycleOperation):
    resolution_note: str = Field(min_length=1)


class ArchitectureBacklogCompletion(GovernanceLifecycleOperation):
    completion_note: str = Field(min_length=1)


class GovernanceRelationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_record_id: str = Field(min_length=1)
    relation_type: str = Field(min_length=1)
    status: str = Field(default="candidate", min_length=1)
    valid_from: date | None = None
    valid_to: date | None = None
    reason: str = Field(min_length=1)
    expected_revision_id: str = Field(min_length=1)
    expected_target_revision_id: str = Field(min_length=1)


class GovernanceSupersession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_record_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    expected_revision_id: str = Field(min_length=1)
    expected_target_revision_id: str = Field(min_length=1)


class IdeaBlockLinkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idea_block_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    display_order: int = Field(default=0, ge=0)
    reason: str = Field(min_length=1)
    expected_revision_id: str = Field(min_length=1)


class EntityLinkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    expected_revision_id: str = Field(min_length=1)


class ArchitectureBacklogCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_key: str = Field(min_length=1)
    title: str = Field(min_length=1)
    pending_correction: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    priority: str = Field(min_length=1)
    target_date: date
    expected_origin_revision_id: str = Field(min_length=1)
