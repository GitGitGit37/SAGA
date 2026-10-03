"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-02
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIM = 384  # fastembed BAAI/bge-small-en-v1.5


def _ts(name: str = "created_at") -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("reliability", sa.Float, nullable=False, server_default="0.5"),
        _ts(),
    )

    op.create_table(
        "assets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("asset_tag", sa.String(32), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("asset_type", sa.String(32), nullable=False),
        sa.Column("model", sa.String(32), nullable=False),
        sa.Column("serial_number", sa.String(64), nullable=False),
        sa.Column("site", sa.String(120), nullable=False),
        sa.Column("commissioned_on", sa.DateTime(timezone=True)),
        sa.Column("attributes", postgresql.JSONB, nullable=False, server_default="{}"),
        _ts(),
    )

    op.create_table(
        "raw_observations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("asset_id", sa.Integer, sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("source", sa.String(255), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("text", sa.Text),
        sa.Column("category", sa.String(64)),
        sa.Column("category_confidence", sa.Float),
        _ts("ingested_at"),
    )
    op.create_index("ix_raw_observations_asset_time", "raw_observations", ["asset_id", "observed_at"])
    op.create_index("ix_raw_observations_kind", "raw_observations", ["kind"])
    op.create_index("ix_raw_observations_category", "raw_observations", ["category"])

    op.create_table(
        "inferences",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("lineage_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("supersedes_id", sa.Integer, sa.ForeignKey("inferences.id")),
        sa.Column("asset_id", sa.Integer, sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("subsystem", sa.String(64)),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("interpretation", sa.Text, nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("is_safety_critical", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("escalated", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("window_start", sa.DateTime(timezone=True)),
        sa.Column("window_end", sa.DateTime(timezone=True)),
        sa.Column("llm_confidence_raw", sa.Float, nullable=False),
        sa.Column("llm_confidence_verified", sa.Float, nullable=False),
        sa.Column("evidence_score", sa.Float, nullable=False),
        sa.Column("base_rate", sa.Float, nullable=False),
        sa.Column("final_confidence", sa.Float, nullable=False),
        sa.Column("weights", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_by", sa.String(64), nullable=False, server_default="pipeline"),
        sa.Column("change_reason", sa.Text),
        _ts(),
        sa.UniqueConstraint("lineage_id", "version", name="uq_inferences_lineage_version"),
    )
    op.create_index("ix_inferences_lineage_id", "inferences", ["lineage_id"])
    op.create_index("ix_inferences_category", "inferences", ["category"])
    op.create_index("ix_inferences_asset_status", "inferences", ["asset_id", "status"])

    op.create_table(
        "evidence_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("inference_id", sa.Integer, sa.ForeignKey("inferences.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("strength", sa.Float, nullable=False),
        sa.Column("verified", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_ref", sa.String(255), nullable=False),
        sa.Column("observation_id", sa.Integer, sa.ForeignKey("raw_observations.id", ondelete="SET NULL")),
        sa.Column("details", postgresql.JSONB, nullable=False, server_default="{}"),
        _ts(),
    )
    op.create_index("ix_evidence_items_inference_id", "evidence_items", ["inference_id"])

    op.create_table(
        "feedback",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("inference_id", sa.Integer, sa.ForeignKey("inferences.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("feedback_type", sa.String(32), nullable=False),
        sa.Column("rationale", sa.Text),
        sa.Column("proposed_category", sa.String(64)),
        sa.Column("proposed_interpretation", sa.Text),
        sa.Column("attached_observation_ids", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("role_weight", sa.Float),
        sa.Column("reliability_at_submission", sa.Float),
        sa.Column("evidence_against", sa.Float),
        sa.Column("weight", sa.Float),
        sa.Column("outcome", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("system_response", sa.Text),
        sa.Column("resulting_inference_id", sa.Integer, sa.ForeignKey("inferences.id")),
        sa.Column("data_verdict", sa.String(16)),
        _ts(),
    )
    op.create_index("ix_feedback_inference_id", "feedback", ["inference_id"])

    op.create_table(
        "embeddings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("owner_type", sa.String(32), nullable=False),
        sa.Column("owner_id", sa.Integer, nullable=False),
        sa.Column("asset_id", sa.Integer, sa.ForeignKey("assets.id", ondelete="CASCADE")),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        _ts(),
        sa.UniqueConstraint("owner_type", "owner_id", name="uq_embeddings_owner"),
    )
    op.create_index("ix_embeddings_asset_id", "embeddings", ["asset_id"])
    op.execute(
        "CREATE INDEX ix_embeddings_hnsw ON embeddings USING hnsw (embedding vector_cosine_ops)"
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer, primary_key=True),
        _ts("ts"),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", sa.Integer, nullable=False),
        sa.Column("before", postgresql.JSONB),
        sa.Column("after", postgresql.JSONB),
        sa.Column("details", postgresql.JSONB, nullable=False, server_default="{}"),
    )
    op.create_index("ix_audit_log_entity", "audit_log", ["entity_type", "entity_id"])


def downgrade() -> None:
    for table in ["audit_log", "embeddings", "feedback", "evidence_items", "inferences",
                  "raw_observations", "assets", "users"]:
        op.drop_table(table)
