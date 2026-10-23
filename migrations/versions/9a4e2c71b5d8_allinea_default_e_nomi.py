"""default e nomi degli indici allineati al DB storico

Da qui in avanti lo schema e' gestito solo da Alembic. Questa revisione
copre le ultime differenze tra le migrazioni e il DB di produzione, che
fino a ottobre 2026 veniva creato a mano all'avvio dell'app:

  - default lato server sulle colonne NOT NULL (le INSERT dell'app non
    valorizzano is_admin, punti, is_attiva... e contano sul default);
  - su PostgreSQL, gli indici e il vincolo creati dal vecchio codice
    vengono rinominati con i nomi che usa Alembic (no-op se non ci sono).

Revision ID: 9a4e2c71b5d8
Revises: 7b2c9e41d0a3
Create Date: 2026-10-23 15:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '9a4e2c71b5d8'
down_revision: Union[str, None] = '7b2c9e41d0a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FALSO, ZERO = 'falso', 'zero'
DEFAULT = {
    'utenti':                    [('is_temp_password', sa.Boolean(), FALSO),
                                  ('is_admin', sa.Boolean(), FALSO)],
    'partite':                   [('pronosticabile', sa.Boolean(), FALSO)],
    'punteggi':                  [('punteggio_totale', sa.Integer(), ZERO)],
    'punteggi_giornata':         [('punti', sa.Integer(), ZERO)],
    'stato_giornata':            [('is_attiva', sa.Boolean(), FALSO),
                                  ('is_in_archivio', sa.Boolean(), FALSO)],
    'stato_pronostici_iniziali': [('is_locked', sa.Boolean(), FALSO)],
}

INDICI_STORICI = [
    ('ix_pg_id_partita', 'ix_pronostici_giornata_id_partita'),
    ('ix_pg_id_utente', 'ix_pronostici_giornata_id_utente'),
    ('ix_pi_id_utente', 'ix_pronostici_iniziali_id_utente'),
    ('ix_punteggi_giornata_utente', 'ix_punteggi_giornata_id_utente'),
]


def _imposta_default(valori):
    for tabella, colonne in DEFAULT.items():
        with op.batch_alter_table(tabella, schema=None) as batch_op:
            for colonna, tipo, chiave in colonne:
                batch_op.alter_column(colonna, existing_type=tipo,
                                      existing_nullable=False,
                                      server_default=valori[chiave])


def upgrade() -> None:
    postgres = op.get_bind().dialect.name == 'postgresql'
    _imposta_default({FALSO: sa.text('false' if postgres else '0'),
                      ZERO: sa.text('0')})

    if postgres:
        for vecchio, nuovo in INDICI_STORICI:
            op.execute(f'ALTER INDEX IF EXISTS {vecchio} RENAME TO {nuovo}')
        op.execute("""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_constraint
                           WHERE conname = 'punteggi_giornata_id_utente_giornata_key') THEN
                    ALTER TABLE punteggi_giornata RENAME CONSTRAINT
                        punteggi_giornata_id_utente_giornata_key TO uq_punteggio_giornata;
                END IF;
            END $$;
        """)


def downgrade() -> None:
    _imposta_default({FALSO: None, ZERO: None})
