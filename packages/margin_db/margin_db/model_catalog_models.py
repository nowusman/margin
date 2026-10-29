from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from margin_db.base import Base, JsonDict, JsonList, utc_now


class ModelConfiguration(Base):
    __tablename__ = "model_configurations"
    __table_args__ = (
        CheckConstraint(
            "purpose in ('extraction', 'vision_ocr', 'answer_synthesis', 'semantic_resolution')",
            name="ck_model_configurations_purpose",
        ),
        CheckConstraint("status in ('active', 'disabled', 'experimental')", name="ck_model_configurations_status"),
        CheckConstraint("context_length >= 0", name="ck_model_configurations_context_length"),
        CheckConstraint("max_tokens >= 0", name="ck_model_configurations_max_tokens"),
        CheckConstraint("temperature_default >= 0", name="ck_model_configurations_temperature_default"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    runtime: Mapped[str] = mapped_column(Text, nullable=False)
    model_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    endpoint_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    secret_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    capabilities: Mapped[dict] = mapped_column(JsonDict, default=dict)
    context_length: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    temperature_default: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    license: Mapped[str] = mapped_column(Text, nullable=False, default="model-specific")
    hardware_profile: Mapped[str] = mapped_column(Text, nullable=False, default="local")
    default_scopes: Mapped[list] = mapped_column(JsonList, default=list)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="disabled")
    last_test_status: Mapped[str | None] = mapped_column(Text)
    last_test_latency_ms: Mapped[int | None] = mapped_column(Integer)
    last_test_detail: Mapped[str | None] = mapped_column(Text)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
