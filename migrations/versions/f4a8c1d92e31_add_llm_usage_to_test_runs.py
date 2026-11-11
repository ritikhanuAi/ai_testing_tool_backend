"""add LLM token usage counters to test_runs

Revision ID: f4a8c1d92e31
Revises: e35dc02a8b22
Create Date: 2026-11-10

"""
from alembic import op
import sqlalchemy as sa


revision = "f4a8c1d92e31"
down_revision = "e35dc02a8b22"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("test_runs", sa.Column("llm_provider", sa.String(length=50), nullable=True))
    op.add_column("test_runs", sa.Column("llm_model", sa.String(length=255), nullable=True))
    for name in (
        "llm_call_count",
        "llm_usage_reported_calls",
        "llm_prompt_tokens",
        "llm_completion_tokens",
        "llm_reasoning_tokens",
        "llm_total_tokens",
    ):
        op.add_column(
            "test_runs",
            sa.Column(name, sa.Integer(), server_default="0", nullable=False),
        )


def downgrade() -> None:
    for name in (
        "llm_total_tokens",
        "llm_reasoning_tokens",
        "llm_completion_tokens",
        "llm_prompt_tokens",
        "llm_usage_reported_calls",
        "llm_call_count",
        "llm_model",
        "llm_provider",
    ):
        op.drop_column("test_runs", name)
