"""add snapshot column to simulation_runs

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
"""

from alembic import op
import sqlalchemy as sa


revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Epic 22, US22.1: every versioned component that influenced this run,
    # captured at run start -- see app/twin/snapshot.py.
    op.add_column("simulation_runs", sa.Column("snapshot", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("simulation_runs", "snapshot")
