"""Test per db_conn() lato Postgres: una connessione "morta" nel pool
(chiusa da Supabase per inattività) va scartata e sostituita, non usata
così com'è — altrimenti la richiesta fallisce con un errore di connessione
(il bug del 6/9/2026).

Usa monkeypatch sugli attributi del modulo già importato (USE_POSTGRES,
_pg_pool) invece di ricaricare db_utils: ricaricarlo romperebbe la
connessione SQLite in-memory condivisa dal resto della suite (vedi
conftest.py), perché app.py/i blueprint restano legati agli oggetti del
modulo originale.
"""

from unittest.mock import MagicMock

import db_utils


def _make_conn(alive=True):
    conn = MagicMock(name='connessione')
    cur = MagicMock(name='cursore')
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    if alive:
        cur.execute = MagicMock()
    else:
        cur.execute = MagicMock(side_effect=Exception('server closed the connection unexpectedly'))
    conn.cursor = MagicMock(return_value=cur)
    return conn


def test_scarta_connessione_morta_e_ne_prende_una_nuova(monkeypatch):
    monkeypatch.setattr(db_utils, 'USE_POSTGRES', True)

    conn_morta = _make_conn(alive=False)
    conn_viva = _make_conn(alive=True)
    fake_pool = MagicMock()
    fake_pool.getconn.side_effect = [conn_morta, conn_viva]
    monkeypatch.setattr(db_utils, '_pg_pool', fake_pool)

    with db_utils.db_conn() as conn:
        assert conn is conn_viva

    # la connessione morta va scartata esplicitamente (close=True)...
    fake_pool.putconn.assert_any_call(conn_morta, close=True)
    # ...e quella viva restituita normalmente al pool a fine richiesta
    fake_pool.putconn.assert_any_call(conn_viva)
    assert fake_pool.getconn.call_count == 2


def test_connessione_viva_non_viene_scartata(monkeypatch):
    monkeypatch.setattr(db_utils, 'USE_POSTGRES', True)

    conn_viva = _make_conn(alive=True)
    fake_pool = MagicMock()
    fake_pool.getconn.side_effect = [conn_viva]
    monkeypatch.setattr(db_utils, '_pg_pool', fake_pool)

    with db_utils.db_conn() as conn:
        assert conn is conn_viva

    assert fake_pool.getconn.call_count == 1
    fake_pool.putconn.assert_called_once_with(conn_viva)
