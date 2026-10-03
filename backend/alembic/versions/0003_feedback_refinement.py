"""feedback refinement fields

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-02
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("feedback", sa.Column("proposed_hypothesis", sa.String(32)))
    op.add_column("feedback", sa.Column("scores", postgresql.JSONB, nullable=False, server_default="{}"))
    op.add_column("feedback", sa.Column("data_as_of", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("feedback", "data_as_of")
    op.drop_column("feedback", "scores")
    op.drop_column("feedback", "proposed_hypothesis")
