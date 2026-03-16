"""add MFA fields to users

Revision ID: 0013
Revises: 0012
Create Date: 2026-03-13
"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("mfa_secret",  sa.String(), nullable=True))
    op.add_column("users", sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("users", sa.Column("data_residency", sa.String(), nullable=False, server_default="TR"))


def downgrade() -> None:
    op.drop_column("users", "data_residency")
    op.drop_column("users", "mfa_enabled")
    op.drop_column("users", "mfa_secret")
