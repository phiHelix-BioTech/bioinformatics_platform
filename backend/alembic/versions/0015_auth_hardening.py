"""auth hardening: email verification, account lockout, password reset

Revision ID: 0015
Revises: 0014
"""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("email_verified", sa.Boolean(), nullable=False,
                                      server_default="false"))
    op.add_column("users", sa.Column("email_verification_token", sa.String(), nullable=True))
    op.add_column("users", sa.Column("password_reset_token", sa.String(), nullable=True))
    op.add_column("users", sa.Column("password_reset_expires",
                                      sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("failed_login_attempts", sa.Integer(),
                                      nullable=False, server_default="0"))
    op.add_column("users", sa.Column("locked_until",
                                      sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_users_password_reset_token", "users", ["password_reset_token"],
                     unique=False)


def downgrade():
    op.drop_index("ix_users_password_reset_token", table_name="users")
    op.drop_column("users", "locked_until")
    op.drop_column("users", "failed_login_attempts")
    op.drop_column("users", "password_reset_expires")
    op.drop_column("users", "password_reset_token")
    op.drop_column("users", "email_verification_token")
    op.drop_column("users", "email_verified")
