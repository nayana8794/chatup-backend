"""add_district_governor_role_and_user_district

Revision ID: b3cfebd77b59
Revises: 0826f3c0b89a
Create Date: 2026-11-02 04:15:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'b3cfebd77b59'
down_revision = '0826f3c0b89a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. PostgreSQL UserRole Enum එකට DISTRICT_GOVERNOR අගය ආරක්ෂිතව එක් කිරීම
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'DISTRICT_GOVERNOR'")

    # 2. users table එකට district column එක එක් කිරීම
    op.add_column('users', sa.Column('district', sa.String(length=100), nullable=True))
    op.create_index(op.f('ix_users_district'), 'users', ['district'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_users_district'), table_name='users')
    op.drop_column('users', 'district')