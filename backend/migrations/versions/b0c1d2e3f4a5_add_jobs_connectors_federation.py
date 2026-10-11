"""add jobs, connectors, business-unit memberships, context access log

Revision ID: b0c1d2e3f4a5
Revises: a9b0c1d2e3f4
"""

from alembic import op
import sqlalchemy as sa


revision = "b0c1d2e3f4a5"
down_revision = "a9b0c1d2e3f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Epic 31.
    op.add_column("context_entities", sa.Column("external_id", sa.String(), nullable=True))
    op.create_index("ix_context_entities_external_id", "context_entities", ["external_id"])
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("run_after", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("locked_by", sa.String(), nullable=True),
        sa.Column("locked_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("dedupe_key", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_jobs_kind", "jobs", ["kind"])
    op.create_index("ix_jobs_status_run_after", "jobs", ["status", "run_after"])
    op.create_index("ix_jobs_dedupe_key", "jobs", ["dedupe_key"])
    op.create_table(
        "connectors",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("schedule_minutes", sa.Integer(), nullable=True),
        sa.Column("owner_business_unit_id", sa.String(), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(), nullable=True),
        sa.Column("last_status", sa.String(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_table(
        "connector_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("connector_id", sa.String(), sa.ForeignKey("connectors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("stats", sa.JSON(), nullable=False),
        sa.Column("impacts", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_connector_runs_connector_id", "connector_runs", ["connector_id"])
    op.create_table(
        "user_business_units",
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column(
            "business_unit_id", sa.String(), sa.ForeignKey("context_entities.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("steward", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_table(
        "context_access_log",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("actor_type", sa.String(), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("process_id", sa.String(), nullable=True),
        sa.Column("pack_id", sa.String(), nullable=True),
        sa.Column("entity_ids", sa.JSON(), nullable=False),
        sa.Column("business_unit_id", sa.String(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
    )
    op.create_index("ix_context_access_log_at", "context_access_log", ["at"])
    op.create_index("ix_context_access_log_process_id", "context_access_log", ["process_id"])


def downgrade() -> None:
    op.drop_table("context_access_log")
    op.drop_table("user_business_units")
    op.drop_table("connector_runs")
    op.drop_table("connectors")
    op.drop_table("jobs")
    op.drop_index("ix_context_entities_external_id", "context_entities")
    op.drop_column("context_entities", "external_id")
