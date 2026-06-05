"""
Utility di accesso al database.

Questo modulo mantiene la stessa interfaccia della V2 per compatibilità:
  - db_conn()      context manager che apre/chiude la connessione
  - db_execute()   esegue una query con parametri (adatta ? a %s per PG)
  - db_fetchone()
  - db_fetchall()
  - db_commit()
  - row_get()      accede a un campo sia da sqlite3.Row che da psycopg2 Row

Viene importato dai blueprint e dai servizi. Non dipende da extensions.py
per non creare circular import con SQLAlchemy.
"""

import os
import logging
from contextlib import contextmanager

log = logging.getLogger('fanta')

# --- Rilevamento backend DB ---
DATABASE_URL = os.environ.get('DATABASE_URL', '')
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

if DATABASE_URL:
    import psycopg2
    import psycopg2.extras
    USE_POSTGRES = True
else:
    import sqlite3
    USE_POSTGRES = False


def _new_connection():
    if USE_POSTGRES:
        return psycopg2.connect(
            DATABASE_URL,
            cursor_factory=psycopg2.extras.RealDictCursor
        )
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'database.db')
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def db_conn():
    """Context manager: chiude SEMPRE la connessione, anche su eccezione."""
    conn = _new_connection()
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            log.exception('Errore chiusura connessione DB')


def db_execute(conn, query: str, params=()):
    """
    Esegue una query adattando i placeholder:
    - SQLite:     ?
    - PostgreSQL: %s
    """
    if USE_POSTGRES:
        query = query.replace('?', '%s')
    cur = conn.cursor()
    cur.execute(query, params)
    return cur


def db_fetchone(conn, query: str, params=()):
    return db_execute(conn, query, params).fetchone()


def db_fetchall(conn, query: str, params=()):
    return db_execute(conn, query, params).fetchall()


def db_commit(conn):
    conn.commit()


def row_get(row, key):
    """Accede a un campo sia da sqlite3.Row che da psycopg2 RealDictRow."""
    if row is None:
        return None
    try:
        return row[key]
    except (KeyError, IndexError):
        return None
