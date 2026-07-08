"""
Fixture condivise per i test pytest.

Usa SQLite in memoria per isolamento totale: un DB vuoto condiviso per
sessione, nessun effetto sull'ambiente reale.
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
        TESTING          = True
        DEBUG            = False
        DATABASE_URL     = ''       # forza SQLite
        TALISMAN_ENABLED = False
        WTF_CSRF_ENABLED = False    # disabilitato per i test di route

    import sqlite3
    import db_utils as _dbu

    # Predispone la connessione in-memory condivisa PRIMA di create_app,
    # così anche il create_tables() interno alla factory usa lo stesso DB.
    _orig_new_conn = _dbu._new_connection
    _shared_conn   = sqlite3.connect(':memory:', check_same_thread=False)
    _shared_conn.row_factory = sqlite3.Row

    class _NonClosingConn:
        """Proxy che condivide un'unica connessione in-memory tra i test.

        db_conn() chiama conn.close() nel finally: senza questo wrapper la
        connessione (e quindi il DB in-memory) verrebbe distrutta dopo il
        primo utilizzo. Qui close() e' un no-op; la connessione reale viene
        chiusa solo nel teardown della fixture.
        """
        def __init__(self, conn):
            object.__setattr__(self, '_conn', conn)

        def close(self):
            pass

        def __getattr__(self, name):
            return getattr(object.__getattribute__(self, '_conn'), name)

    _proxy_conn = _NonClosingConn(_shared_conn)
    _dbu._new_connection = lambda: _proxy_conn

    from app import create_app
    application = create_app(TestConfig())

    yield application

    _dbu._new_connection = _orig_new_conn
    _shared_conn.close()


@pytest.fixture
def client(app):
    # Function-scoped: ogni test ottiene un client con cookie jar pulito
    # (evita contaminazione della sessione tra i test).
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
                   (row_get(row, 'id'),))
        db_commit(conn)
    return row_get(row, 'id')
