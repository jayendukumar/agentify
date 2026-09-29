"""add twin_environments table and scenario environment references

Revision ID: a1b2c3d4e5f6
Revises: f1a2b3c4d5e6
"""

from alembic import op
import sqlalchemy as sa


revision = "a1b2c3d4e5f6"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Epic 21, US21.1/US21.2/US21.4: a named, versioned bundle of systems/
    # datasets/personas/events plus a per-system fault-injection catalog.
    op.create_table(
        "twin_environments",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("process_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("systems", sa.JSON(), nullable=False),
        sa.Column("datasets", sa.JSON(), nullable=False),
        sa.Column("personas", sa.JSON(), nullable=False),
        sa.Column("events", sa.JSON(), nullable=False),
        sa.Column("faults", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["process_id"], ["processes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_twin_environments_process_id"), "twin_environments", ["process_id"], unique=False)

    # Epic 21, US21.3: additive, nullable -- every scenario created before
    # this epic keeps working unchanged with no environment reference.
    op.add_column("twin_scenarios", sa.Column("environment_id", sa.String(), nullable=True))
    op.add_column("twin_scenarios", sa.Column("environment_version", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_twin_scenarios_environment_id", "twin_scenarios", "twin_environments", ["environment_id"], ["id"],
        ondelete="SET NULL",
    )

    op.add_column("orchestration_scenarios", sa.Column("environment_id", sa.String(), nullable=True))
    op.add_column("orchestration_scenarios", sa.Column("environment_version", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_orchestration_scenarios_environment_id", "orchestration_scenarios", "twin_environments",
        ["environment_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_orchestration_scenarios_environment_id", "orchestration_scenarios", type_="foreignkey")
    op.drop_column("orchestration_scenarios", "environment_version")
    op.drop_column("orchestration_scenarios", "environment_id")

    op.drop_constraint("fk_twin_scenarios_environment_id", "twin_scenarios", type_="foreignkey")
    op.drop_column("twin_scenarios", "environment_version")
    op.drop_column("twin_scenarios", "environment_id")

    op.drop_index(op.f("ix_twin_environments_process_id"), table_name="twin_environments")
    op.drop_table("twin_environments")
