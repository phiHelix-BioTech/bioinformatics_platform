"""create patients and samples tables; add sample_id to jobs

Revision ID: 0016
Revises: 0015
"""
from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "patients",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False, index=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("sex", sa.String(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_patients_user_id", "patients", ["user_id"])

    op.create_table(
        "samples",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("patient_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("sample_type", sa.String(), nullable=False),
        sa.Column("collection_date", sa.Date(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_samples_patient_id", "samples", ["patient_id"])
    op.create_index("ix_samples_user_id", "samples", ["user_id"])

    op.add_column("jobs", sa.Column("sample_id", sa.String(), nullable=True))
    op.create_index("ix_jobs_sample_id", "jobs", ["sample_id"])


def downgrade():
    op.drop_index("ix_jobs_sample_id", table_name="jobs")
    op.drop_column("jobs", "sample_id")
    op.drop_index("ix_samples_user_id", table_name="samples")
    op.drop_index("ix_samples_patient_id", table_name="samples")
    op.drop_table("samples")
    op.drop_index("ix_patients_user_id", table_name="patients")
    op.drop_table("patients")
