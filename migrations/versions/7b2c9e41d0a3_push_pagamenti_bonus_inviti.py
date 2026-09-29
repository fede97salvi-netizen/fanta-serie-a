"""push multi-dispositivo, pagamenti, bonus finale, blocco login, inviti

Allinea Alembic a quanto create_tables() gestisce a runtime:
  - partite: promemoria_inviato, promemoria_scadenza_inviato
  - utenti: tentativi_falliti, bloccato_fino
  - punteggi: bonus_finale
  - tabelle push_subscriptions (una riga per dispositivo), pagamenti, impostazioni

Sul DB di produzione, gia' allineato da create_tables(), NON va eseguita:
lanciare una sola volta `alembic stamp head`.

Revision ID: 7b2c9e41d0a3
Revises: 46f66cf35f6a
Create Date: 2026-09-29 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = '7b2c9e41d0a3'
down_revision: Union[str, None] = '46f66cf35f6a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('partite', schema=None) as batch_op:
        batch_op.add_column(sa.Column('promemoria_inviato', sa.Boolean(),
                                      nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('promemoria_scadenza_inviato', sa.Boolean(),
                                      nullable=False, server_default=sa.false()))

    with op.batch_alter_table('utenti', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tentativi_falliti', sa.Integer(),
                                      nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('bloccato_fino', sa.Text(), nullable=True))

    with op.batch_alter_table('punteggi', schema=None) as batch_op:
        batch_op.add_column(sa.Column('bonus_finale', sa.Integer(),
                                      nullable=False, server_default='0'))

    op.create_table('push_subscriptions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('id_utente', sa.Integer(), nullable=True),
    sa.Column('subscription_info', sa.JSON().with_variant(JSONB(), 'postgresql'),
              nullable=False),
    sa.Column('nome_utente', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=True,
              server_default=sa.func.current_timestamp()),
    sa.Column('endpoint', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['id_utente'], ['utenti.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('push_subscriptions', schema=None) as batch_op:
        batch_op.create_index('ux_push_subscriptions_endpoint', ['endpoint'], unique=True)
        batch_op.create_index('ix_push_subscriptions_utente', ['id_utente'], unique=False)

    op.create_table('pagamenti',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('id_utente', sa.Integer(), nullable=False),
    sa.Column('ha_pagato', sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column('importo', sa.Float(), nullable=True),
    sa.Column('data_pagamento', sa.Text(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['id_utente'], ['utenti.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('id_utente')
    )

    op.create_table('impostazioni',
    sa.Column('chiave', sa.Text(), nullable=False),
    sa.Column('valore', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('chiave')
    )


def downgrade() -> None:
    op.drop_table('impostazioni')
    op.drop_table('pagamenti')
    with op.batch_alter_table('push_subscriptions', schema=None) as batch_op:
        batch_op.drop_index('ix_push_subscriptions_utente')
        batch_op.drop_index('ux_push_subscriptions_endpoint')
    op.drop_table('push_subscriptions')

    with op.batch_alter_table('punteggi', schema=None) as batch_op:
        batch_op.drop_column('bonus_finale')

    with op.batch_alter_table('utenti', schema=None) as batch_op:
        batch_op.drop_column('bloccato_fino')
        batch_op.drop_column('tentativi_falliti')

    with op.batch_alter_table('partite', schema=None) as batch_op:
        batch_op.drop_column('promemoria_scadenza_inviato')
        batch_op.drop_column('promemoria_inviato')
