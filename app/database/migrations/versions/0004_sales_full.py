"""sales agent full version: files, payments, reminders

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-05 12:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('leads', schema=None) as batch_op:
        batch_op.add_column(sa.Column('prepayment_due', sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column('currency', sa.String(length=8), nullable=True))
        batch_op.add_column(sa.Column('payment_link', sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column('prepaid_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('reminder_count', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('last_reminder_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('extra', sa.JSON(), nullable=True))
    with op.batch_alter_table('leads', schema=None) as batch_op:
        batch_op.alter_column('reminder_count', server_default=None)

    op.create_table('lead_files',
    sa.Column('lead_id', sa.Integer(), nullable=False),
    sa.Column('channel', sa.String(length=20), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('file_name', sa.String(length=200), nullable=False),
    sa.Column('mime', sa.String(length=100), nullable=True),
    sa.Column('size', sa.Integer(), nullable=False),
    sa.Column('path', sa.String(length=500), nullable=True),
    sa.Column('caption', sa.Text(), nullable=True),
    sa.Column('transcript', sa.Text(), nullable=True),
    sa.Column('external_message_id', sa.String(length=200), nullable=True),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('channel', 'external_message_id', name='uq_lead_file_external')
    )
    with op.batch_alter_table('lead_files', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_lead_files_lead_id'), ['lead_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('lead_files', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_lead_files_lead_id'))
    op.drop_table('lead_files')
    with op.batch_alter_table('leads', schema=None) as batch_op:
        for col in ('extra', 'last_reminder_at', 'reminder_count', 'prepaid_at', 'payment_link', 'currency', 'prepayment_due'):
            batch_op.drop_column(col)
