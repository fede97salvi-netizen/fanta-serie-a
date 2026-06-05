"""
Fixture condivise per i test pytest.

Usa SQLite in memoria per isolamento totale: ogni test
ottiene un DB vuoto, nessun effetto sull'ambiente reale.
"""

import pytest
from werkzeug.security import generate_password_hash

# Assicura che il DB in-memory venga usato prima che l'app venga importata
import os
os.environ.setdefault('SECRET_KEY', 'chiave-di-test-non-sicura')


@pytest.fixture(scope='session')
def app():
    """Crea l'app Flask con configurazione di test."""
    from config import DevelopmentConfig

    class TestConfig(DevelopmentConfig):
        TESTING        = True
        DEBUG          = False
        DATABASE_URL   = ''       # forza SQLite
        TALISMAN_ENABLED = False
        WTF_CSRF_ENABLED = False  # disabilitato per semplicità nei test di route

    from app import create_app
    application = create_app(TestConfig())

    # Sovrascrive il DB con SQLite puro in memoria
    from db_utils import db_conn, db_execute, db_fetchone, db_fetchall, db_commit
    import sqlite3
    import db_utils as _dbu

    _orig_new_conn = _dbu._new_connection
    _shared_conn   = sqlite3.connect(':memory:', check_same_thread=False)
    _shared_conn.row_factory = sqlite3.Row

    def _in_memory_conn():
        return _shared_conn

    _dbu._new_connection = _in_memory_conn

    with application.app_context():
        from app import create_tables
        create_tables()

    yield application

    _dbu._new_connection = _orig_new_conn
    _shared_conn.close()


@pytest.fixture(scope='session')
def client(app):
    return app.test_client()


@pytest.fixture(scope='session')
def runner(app):
    return app.test_cli_runner()


@pytest.fixture
def db_session(app):
    """Restituisce funzioni db_* pronte all'uso nei test."""
    from db_utils import db_conn, db_execute, db_fetchone, db_fetchall, db_commit
    return {
        'conn':     db_conn,
        'execute':  db_execute,
        'fetchone': db_fetchone,
        'fetchall': db_fetchall,
        'commit':   db_commit,
    }


# ─── Helper per creare utenti di test ────────────────────────────────────────

def _crea_utente(nome: str, password: str = 'test123',
                 is_admin: bool = False) -> int:
    """Crea un utente nel DB di test e restituisce il suo id."""
    from db_utils import db_conn, db_execute, db_fetchone, db_commit, row_get
    pw_hash = generate_password_hash(password)
    with db_conn() as conn:
        db_execute(conn,
                   'INSERT OR IGNORE INTO utenti '
                   '(nome_utente, password, is_admin) VALUES (?, ?, ?)',
                   (nome, pw_hash, 1 if is_admin else 0))
        db_commit(conn)
        row = db_fetchone(conn,
                          'SELECT id FROM utenti WHERE nome_utente = ?', (nome,))
        db_execute(conn,
                   'INSERT OR IGNORE INTO punteggi (id_utente, punteggio_totale) '
                   'VALUES (?, 0)',
                   (row['id'],))
        db_commit(conn)
    return row['id']
