"""Strict version-one HTTP contracts for governed retrieval and Ask Margin."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RetrievalFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain_ids: list[str] = Field(default_factory=list)
    category_ids: list[str] = Field(default_factory=list)
    entity_ids: list[str] = Field(default_factory=list)
    entity_terms: list[str] = Field(default_factory=list)
    entity_types: list[str] = Field(default_factory=list)
    relationship_types: list[str] = Field(default_factory=list)
    idea_block_types: list[str] = Field(default_factory=list)
    source_document_ids: list[str] = Field(default_factory=list)
    sensitivities: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=lambda: ["verified"])
    valid_from: date | None = None
    valid_to: date | None = None
    governance_record_types: list[str] = Field(default_factory=list)
    governance_statuses: list[str] = Field(default_factory=list)
    architecture_backlog_states: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_dates(self) -> RetrievalFilters:
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise ValueError("valid_from cannot be later than valid_to")
        return self


class RetrievalExpansion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relation_types: list[str] = Field(default_factory=list)
    depth: int = Field(default=0, ge=0, le=2)
    limit: int = Field(default=0, ge=0, le=20)

    @model_validator(mode="after")
    def validate_bounded_expansion(self) -> RetrievalExpansion:
        if self.depth == 0 and (self.relation_types or self.limit):
            raise ValueError("expansion depth must be positive when expansion options are provided")
        if self.depth > 0 and (not self.relation_types or self.limit == 0):
            raise ValueError("expansion requires relation_types and a positive limit")
        return self


class RetrievalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2_000)
    filters: RetrievalFilters = Field(default_factory=RetrievalFilters)
    top_k: int = Field(default=8, ge=1, le=20)
    expand: RetrievalExpansion = Field(default_factory=RetrievalExpansion)
    output_language: str = Field(default="", max_length=64)
    context_budget_chars: int | None = Field(default=None, ge=1_024, le=1_048_576)
    thread_id: str | None = Field(default=None, max_length=200)
