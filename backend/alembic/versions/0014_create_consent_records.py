"""create consent_records table

Revision ID: 0014
Revises: 0013
Create Date: 2026-03-13
"""
from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "consent_records",
        sa.Column("id",           sa.String(), nullable=False, primary_key=True),
        sa.Column("user_id",      sa.String(), nullable=False),
        sa.Column("consent_type", sa.String(), nullable=False),
        sa.Column("consented",    sa.Boolean(), nullable=False),
        sa.Column("ip_address",   sa.String(), nullable=True),
        sa.Column("user_agent",   sa.String(), nullable=True),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_consent_records_user_id",     "consent_records", ["user_id"])
    op.create_index("ix_consent_records_consented_at","consent_records", ["consented_at"])


def downgrade() -> None:
    op.drop_index("ix_consent_records_consented_at", table_name="consent_records")
    op.drop_index("ix_consent_records_user_id",     table_name="consent_records")
    op.drop_table("consent_records")
