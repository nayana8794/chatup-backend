# chatup/backend/alembic/versions/20261020_add_event_duty_shifts_and_guests.py
"""add event duty shifts and guests

Revision ID: 20261020_duty_shifts
Revises: 
Create Date: 2026-10-20 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '20261020_duty_shifts'
down_revision = '1f082c04736d'  # Or your latest revision ID if using chained revisions
branch_labels = None
depends_on = None

def upgrade() -> None:
    # Add duty_shifts and allows_guests to events table
    op.add_column('events', sa.Column('allows_guests', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('events', sa.Column('duty_shifts', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=True))
    
    # Add guest_count and duty_shift_id to event_rsvps table
    op.add_column('event_rsvps', sa.Column('guest_count', sa.Integer(), server_default='0', nullable=False))
    op.add_column('event_rsvps', sa.Column('duty_shift_id', sa.String(length=100), nullable=True))

def downgrade() -> None:
    op.drop_column('event_rsvps', 'duty_shift_id')
    op.drop_column('event_rsvps', 'guest_count')
    op.drop_column('events', 'duty_shifts')
    op.drop_column('events', 'allows_guests')