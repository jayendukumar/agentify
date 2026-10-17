"""add internal triggers, client scoping and signed callbacks to the agent runtime

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9

Epic 39, US39.4: which trigger started an execution (the API or an
internal one through the job queue), the deployments a runtime client may
start, and the secret its callbacks are signed with.
"""

import sqlalchemy as sa
from alembic import op

revision = "f5a6b7c8d9e0"
down_revision = "e4f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_executions", sa.Column("trigger", sa.String(), nullable=False, server_default="api"))
    op.add_column("runtime_clients", sa.Column("deployment_ids", sa.JSON(), nullable=True))
    op.add_column("runtime_clients", sa.Column("signing_secret", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("runtime_clients", "signing_secret")
    op.drop_column("runtime_clients", "deployment_ids")
    op.drop_column("agent_executions", "trigger")
