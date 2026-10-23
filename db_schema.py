"""Schema del database: unica fonte e' Alembic (cartella migrations/).

All'avvio l'app porta il DB all'ultima revisione con `alembic upgrade head`.
Per cambiare lo schema: modifica models.py, poi
    alembic revision --autogenerate -m "descrizione"
controlla il file generato in migrations/versions/ e fai il deploy.
"""

import logging
import os

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import NullPool

import db_utils

log = logging.getLogger('fanta')

ROOT = os.path.dirname(os.path.abspath(__file__))

# Ultima revisione equivalente allo schema che il vecchio codice dell'app
# creava a mano all'avvio. Un DB con le tabelle ma senza alembic_version
# e' stato creato cosi': viene marcato a questa revisione e da li' si
# applicano solo le migrazioni successive.
REVISIONE_SCHEMA_STORICO = '7b2c9e41d0a3'

# Chiave del lock di PostgreSQL: con piu' processi gunicorn in avvio
# insieme, le migrazioni girano una sola volta alla volta.
_CHIAVE_LOCK = 20260923


def _engine():
    if db_utils.USE_POSTGRES:
        return create_engine(db_utils.DATABASE_URL, poolclass=NullPool)
    # SQLite: stessa connessione dell'app (nei test e' il DB in memoria condiviso)
    return create_engine('sqlite://', creator=lambda: db_utils._new_connection(),
                         poolclass=NullPool)


def _config_alembic(connessione) -> Config:
    cfg = Config()
    cfg.set_main_option('script_location', os.path.join(ROOT, 'migrations'))
    cfg.attributes['connection'] = connessione
    return cfg


def aggiorna_schema() -> None:
    engine = _engine()
    try:
        with engine.begin() as conn:
            if db_utils.USE_POSTGRES:
                conn.execute(text('SELECT pg_advisory_xact_lock(:k)'), {'k': _CHIAVE_LOCK})
            cfg = _config_alembic(conn)
            tabelle = set(inspect(conn).get_table_names())
            if 'alembic_version' not in tabelle and 'utenti' in tabelle:
                log.info('DB creato dal vecchio codice: marcato alla revisione %s.',
                         REVISIONE_SCHEMA_STORICO)
                command.stamp(cfg, REVISIONE_SCHEMA_STORICO)
            command.upgrade(cfg, 'head')
    finally:
        engine.dispose()
