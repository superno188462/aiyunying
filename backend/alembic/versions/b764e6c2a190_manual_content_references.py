"""Allow content workflows created from manually supplied references.

Revision ID: b764e6c2a190
Revises: a481f8e3f209
"""
from __future__ import annotations

from alembic import op

revision = "b764e6c2a190"
down_revision = "a481f8e3f209"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("content_workflows") as batch_op:
        batch_op.alter_column("account_id", nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("content_workflows") as batch_op:
        batch_op.alter_column("account_id", nullable=False)
