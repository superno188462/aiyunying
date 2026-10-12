"""Add persisted, reviewed content workflows.

Revision ID: a481f8e3f209
Revises: 60cd5c95fde1
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a481f8e3f209"
down_revision = "60cd5c95fde1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "content_workflows",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("platform_accounts.id"), nullable=False),
        sa.Column("topic", sa.String(120), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("references", sa.JSON(), nullable=False),
        sa.Column("candidates", sa.JSON(), nullable=False),
        sa.Column("selected_topic", sa.JSON(), nullable=False),
        sa.Column("draft_title", sa.String(300), nullable=False),
        sa.Column("draft_body", sa.Text(), nullable=False),
        sa.Column("draft_versions", sa.JSON(), nullable=False),
        sa.Column("cards", sa.JSON(), nullable=False),
        sa.Column("xhs_title", sa.String(300), nullable=False),
        sa.Column("xhs_caption", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_content_workflows_user_id", "content_workflows", ["user_id"])
    op.create_index("ix_content_workflows_stage", "content_workflows", ["stage"])


def downgrade() -> None:
    op.drop_index("ix_content_workflows_stage", table_name="content_workflows")
    op.drop_index("ix_content_workflows_user_id", table_name="content_workflows")
    op.drop_table("content_workflows")
