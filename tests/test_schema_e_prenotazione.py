"""Schema gestito da Alembic e prenotazione atomica dei promemoria."""

import sqlite3

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import NullPool

import db_schema
import db_utils
from extensions import db
import models  # noqa: F401 — registra i modelli su db.metadata
from tests.test_promemoria_partite import _crea_partita
from db_utils import db_conn


def _db_su_file(monkeypatch, path):
    monkeypatch.setattr(db_utils, '_new_connection', lambda: sqlite3.connect(path))
    return create_engine('sqlite://', creator=lambda: sqlite3.connect(path),
                         poolclass=NullPool)


def test_migrazioni_e_modelli_coincidono(tmp_path, monkeypatch):
    engine = _db_su_file(monkeypatch, str(tmp_path / 'nuovo.db'))
    db_schema.aggiorna_schema()
    with engine.connect() as conn:
        differenze = compare_metadata(MigrationContext.configure(conn), db.metadata)
    assert differenze == [], (
        'models.py e migrations/ non coincidono: genera una migrazione con '
        '`alembic revision --autogenerate`')


def test_db_storico_senza_alembic_version_viene_marcato_e_aggiornato(tmp_path, monkeypatch):
    engine = _db_su_file(monkeypatch, str(tmp_path / 'storico.db'))
    # Simula il DB di produzione: schema allineato alla revisione storica,
    # creato senza Alembic (quindi senza la tabella alembic_version).
    with engine.begin() as conn:
        command.upgrade(db_schema._config_alembic(conn), db_schema.REVISIONE_SCHEMA_STORICO)
        conn.execute(text('DROP TABLE alembic_version'))

    db_schema.aggiorna_schema()

    with engine.connect() as conn:
        versione = conn.execute(text('SELECT version_num FROM alembic_version')).scalar()
        head = ScriptDirectory.from_config(
            db_schema._config_alembic(None)).get_current_head()
        assert versione == head
        assert 'utenti' in inspect(conn).get_table_names()


def test_una_partita_si_prenota_una_volta_sola(app):
    from invia_notifiche import _prenota_partita
    with db_conn() as conn:
        pid = _crea_partita(conn, giornata=40, ore_al_calcio_dinizio=20)
        assert _prenota_partita(conn, pid, 'promemoria_inviato') is True
        # secondo scheduler arrivato in contemporanea: non deve reinviare
        assert _prenota_partita(conn, pid, 'promemoria_inviato') is False
        # l'altro flag e' indipendente
        assert _prenota_partita(conn, pid, 'promemoria_scadenza_inviato') is True
