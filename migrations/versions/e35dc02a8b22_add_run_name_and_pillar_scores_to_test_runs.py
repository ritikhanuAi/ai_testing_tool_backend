"""add run_name and pillar scores to test_runs

Revision ID: e35dc02a8b22
Revises:
Create Date: 2026-11-03

"""
from alembic import op
import sqlalchemy as sa

revision = 'e35dc02a8b22'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('test_runs', sa.Column('run_name', sa.String(length=255), nullable=True))
    op.add_column('test_runs', sa.Column('functional_score', sa.Float(), nullable=True))
    op.add_column('test_runs', sa.Column('security_score', sa.Float(), nullable=True))
    op.add_column('test_runs', sa.Column('performance_score', sa.Float(), nullable=True))
    op.add_column('test_runs', sa.Column('accessibility_score', sa.Float(), nullable=True))
    op.create_index('ix_test_runs_run_name', 'test_runs', ['run_name'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_test_runs_run_name', table_name='test_runs')
    op.drop_column('test_runs', 'accessibility_score')
    op.drop_column('test_runs', 'performance_score')
    op.drop_column('test_runs', 'security_score')
    op.drop_column('test_runs', 'functional_score')
    op.drop_column('test_runs', 'run_name')
