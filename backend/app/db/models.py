import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.enums import FeedbackOutcome, InferenceStatus
from app.settings import get_settings

EMBEDDING_DIM = get_settings().embedding_dim


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(32))  # UserRole
    reliability: Mapped[float] = mapped_column(Float, default=0.5)
    created_at: Mapped[datetime] = _now()

    feedback: Mapped[list["Feedback"]] = relationship(back_populates="user")


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_tag: Mapped[str] = mapped_column(String(32), unique=True)  # e.g. "EX-320-A"
    name: Mapped[str] = mapped_column(String(120))
    asset_type: Mapped[str] = mapped_column(String(32))  # AssetType
    model: Mapped[str] = mapped_column(String(32))  # e.g. "320"
    serial_number: Mapped[str] = mapped_column(String(64))
    site: Mapped[str] = mapped_column(String(120))
    commissioned_on: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _now()

    observations: Mapped[list["RawObservation"]] = relationship(back_populates="asset")
    inferences: Mapped[list["Inference"]] = relationship(back_populates="asset")


class RawObservation(Base):
    """Anything ingested about an asset: a telematics row, a fault code, a note."""

    __tablename__ = "raw_observations"
    __table_args__ = (Index("ix_raw_observations_asset_time", "asset_id", "observed_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str] = mapped_column(String(32), index=True)  # ObservationKind
    source: Mapped[str] = mapped_column(String(255))  # file name / feed / "seed"
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    text: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(64), index=True)  # set by categorizer
    category_confidence: Mapped[float | None] = mapped_column(Float)
    ingested_at: Mapped[datetime] = _now()

    asset: Mapped[Asset] = relationship(back_populates="observations")


class Inference(Base):
    """A versioned belief about an asset. Rows are never updated in substance:
    a revision inserts a new row with the same lineage_id and version + 1,
    and the old row's status becomes 'superseded'."""

    __tablename__ = "inferences"
    __table_args__ = (
        UniqueConstraint("lineage_id", "version", name="uq_inferences_lineage_version"),
        Index("ix_inferences_asset_status", "asset_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    lineage_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=uuid.uuid4, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("inferences.id"))
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))

    category: Mapped[str] = mapped_column(String(64), index=True)
    subsystem: Mapped[str | None] = mapped_column(String(64))
    hypothesis: Mapped[str] = mapped_column(String(32), default="degradation")  # Hypothesis
    title: Mapped[str] = mapped_column(String(255))
    interpretation: Mapped[str] = mapped_column(Text)
    recommended_action: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default=InferenceStatus.active)
    is_safety_critical: Mapped[bool] = mapped_column(Boolean, default=False)
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)

    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Confidence breakdown: final = w_e*evidence + w_l*verified_llm + w_h*base_rate
    llm_confidence_raw: Mapped[float] = mapped_column(Float)
    llm_confidence_verified: Mapped[float] = mapped_column(Float)
    evidence_score: Mapped[float] = mapped_column(Float)
    base_rate: Mapped[float] = mapped_column(Float)
    final_confidence: Mapped[float] = mapped_column(Float)
    weights: Mapped[dict[str, float]] = mapped_column(JSONB, default=dict)

    created_by: Mapped[str] = mapped_column(String(64), default="pipeline")  # pipeline | refinement
    change_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()

    asset: Mapped[Asset] = relationship(back_populates="inferences")
    evidence: Mapped[list["EvidenceItem"]] = relationship(
        back_populates="inference", cascade="all, delete-orphan"
    )
    feedback: Mapped[list["Feedback"]] = relationship(
        back_populates="inference", foreign_keys="Feedback.inference_id"
    )


class EvidenceItem(Base):
    __tablename__ = "evidence_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    inference_id: Mapped[int] = mapped_column(ForeignKey("inferences.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # EvidenceKind
    direction: Mapped[str] = mapped_column(String(16))  # EvidenceDirection
    description: Mapped[str] = mapped_column(Text)
    strength: Mapped[float] = mapped_column(Float)  # 0..1
    verified: Mapped[bool] = mapped_column(Boolean, default=True)

    # Where it came from: an observation row, a config rule, or another inference.
    source_type: Mapped[str] = mapped_column(String(32))  # observation | config | inference | feedback
    source_ref: Mapped[str] = mapped_column(String(255))
    observation_id: Mapped[int | None] = mapped_column(ForeignKey("raw_observations.id", ondelete="SET NULL"))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _now()

    inference: Mapped[Inference] = relationship(back_populates="evidence")


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    inference_id: Mapped[int] = mapped_column(ForeignKey("inferences.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    feedback_type: Mapped[str] = mapped_column(String(32))  # FeedbackType
    rationale: Mapped[str | None] = mapped_column(Text)
    proposed_category: Mapped[str | None] = mapped_column(String(64))
    proposed_interpretation: Mapped[str | None] = mapped_column(Text)
    attached_observation_ids: Mapped[list[int]] = mapped_column(JSONB, default=list)

    # Weighting snapshot at submission time (for the audit trail).
    role_weight: Mapped[float | None] = mapped_column(Float)
    reliability_at_submission: Mapped[float | None] = mapped_column(Float)
    evidence_against: Mapped[float | None] = mapped_column(Float)
    weight: Mapped[float | None] = mapped_column(Float)

    outcome: Mapped[str] = mapped_column(String(32), default=FeedbackOutcome.pending)
    system_response: Mapped[str | None] = mapped_column(Text)
    resulting_inference_id: Mapped[int | None] = mapped_column(ForeignKey("inferences.id"))
    data_verdict: Mapped[str | None] = mapped_column(String(16))  # DataVerdict, set later
    created_at: Mapped[datetime] = _now()

    inference: Mapped[Inference] = relationship(back_populates="feedback", foreign_keys=[inference_id])
    user: Mapped[User] = relationship(back_populates="feedback")


class Embedding(Base):
    __tablename__ = "embeddings"
    __table_args__ = (UniqueConstraint("owner_type", "owner_id", name="uq_embeddings_owner"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_type: Mapped[str] = mapped_column(String(32))  # inference | observation
    owner_id: Mapped[int] = mapped_column(Integer)
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
    model: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = _now()


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_entity", "entity_type", "entity_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = _now()
    actor: Mapped[str] = mapped_column(String(64))  # "system" | "user:<id>"
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[int] = mapped_column(Integer)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
