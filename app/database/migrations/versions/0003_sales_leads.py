"""sales agent: leads and lead messages

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-31 12:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None

STAGES = ('new', 'need', 'quantity', 'name', 'phone', 'address', 'confirm', 'won', 'handoff', 'lost')


def upgrade() -> None:
    op.create_table('leads',
    sa.Column('channel', sa.String(length=20), nullable=False),
    sa.Column('external_id', sa.String(length=120), nullable=False),
    sa.Column('language', sa.String(length=8), nullable=False),
    sa.Column('stage', sa.Enum(*STAGES, name='lead_stage', native_enum=False, length=32), nullable=False),
    sa.Column('display_name', sa.String(length=200), nullable=True),
    sa.Column('name', sa.String(length=200), nullable=True),
    sa.Column('phone', sa.String(length=50), nullable=True),
    sa.Column('address', sa.String(length=500), nullable=True),
    sa.Column('product_id', sa.String(length=64), nullable=True),
    sa.Column('product_name', sa.String(length=200), nullable=True),
    sa.Column('unit_price', sa.BigInteger(), nullable=True),
    sa.Column('quantity', sa.Integer(), nullable=True),
    sa.Column('discount_requested', sa.Boolean(), nullable=False),
    sa.Column('order_id', sa.Integer(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('last_message_at', sa.DateTime(), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('leads', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_leads_channel'), ['channel'], unique=False)
        batch_op.create_index(batch_op.f('ix_leads_external_id'), ['external_id'], unique=False)

    op.create_table('lead_messages',
    sa.Column('lead_id', sa.Integer(), nullable=False),
    sa.Column('channel', sa.String(length=20), nullable=False),
    sa.Column('direction', sa.String(length=3), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('external_message_id', sa.String(length=200), nullable=True),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('channel', 'external_message_id', name='uq_lead_message_external')
    )
    with op.batch_alter_table('lead_messages', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_lead_messages_lead_id'), ['lead_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('lead_messages', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_lead_messages_lead_id'))
    op.drop_table('lead_messages')
    with op.batch_alter_table('leads', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_leads_external_id'))
        batch_op.drop_index(batch_op.f('ix_leads_channel'))
    op.drop_table('leads')
