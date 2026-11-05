"""payment transactions (Payme, Click)

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-05 18:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('payment_transactions',
    sa.Column('provider', sa.String(length=10), nullable=False),
    sa.Column('external_id', sa.String(length=64), nullable=False),
    sa.Column('lead_id', sa.Integer(), nullable=False),
    sa.Column('amount', sa.BigInteger(), nullable=False),
    sa.Column('state', sa.Integer(), nullable=False),
    sa.Column('create_time', sa.BigInteger(), nullable=False),
    sa.Column('perform_time', sa.BigInteger(), nullable=False),
    sa.Column('cancel_time', sa.BigInteger(), nullable=False),
    sa.Column('reason', sa.Integer(), nullable=True),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider', 'external_id', name='uq_payment_tx_external')
    )
    with op.batch_alter_table('payment_transactions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_payment_transactions_lead_id'), ['lead_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('payment_transactions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_payment_transactions_lead_id'))
    op.drop_table('payment_transactions')
