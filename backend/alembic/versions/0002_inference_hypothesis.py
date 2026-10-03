"""inference hypothesis and recommended action

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-02
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("inferences", sa.Column("hypothesis", sa.String(32), nullable=False,
                                          server_default="degradation"))
    op.add_column("inferences", sa.Column("recommended_action", sa.Text))


def downgrade() -> None:
    op.drop_column("inferences", "recommended_action")
    op.drop_column("inferences", "hypothesis")
