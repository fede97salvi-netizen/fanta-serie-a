"""
Test di regressione per i bug critici corretti (luglio 2026).

Bloccano il ripetersi di:
  - root '/' 404 e home in errore 500 (refactor V3)
  - /admin 500 con giornata attiva (endpoint reminder disallineato)
  - /attiva-giornata rotto su SQLite (ON CONFLICT solo Postgres)
  - login che non autenticava davvero
  - assenza pagina 404 personalizzata e guard admin
"""

from tests.conftest import _crea_utente
from db_utils import db_conn, db_execute, db_fetchone, db_commit, row_get


def test_root_visitatore_200(client):
    r = client.get('/')
    assert r.status_code == 200
    assert 'FantaSerieA' in r.data.decode('utf-8')


def test_root_utente_autenticato_mostra_dashboard(client):
    _crea_utente('reg_home')
    with client.session_transaction() as s:
        s['nome_utente'] = 'reg_home'
        s['is_admin'] = False
    r = client.get('/')
    assert r.status_code == 200
    assert 'Bentornato' in r.data.decode('utf-8')


def test_login_autentica_e_reindirizza(client):
    _crea_utente('reg_login', 'pw123456')
    r = client.post('/login',
                    data={'nome_utente': 'reg_login', 'password': 'pw123456'})
    assert r.status_code == 302  # redirect a home dopo login riuscito
    with client.session_transaction() as s:
        assert s.get('nome_utente') == 'reg_login'


def test_admin_dashboard_con_giornata_attiva(client):
    """Prima andava in 500: template chiamava un endpoint reminder inesistente."""
    _crea_utente('reg_admin', is_admin=True)
    with db_conn() as conn:
        db_execute(conn, 'INSERT OR IGNORE INTO stato_giornata '
                         '(giornata, is_attiva) VALUES (91, 1)')
        db_execute(conn, 'UPDATE stato_giornata SET is_attiva = 1 WHERE giornata = 91')
        db_execute(conn, 'INSERT OR IGNORE INTO partite '
                         '(giornata, squadra_casa, squadra_ospite, pronosticabile, '
                         'data_ora_partita) VALUES (91, "A", "B", 1, "2020-01-01T12:00")')
        db_commit(conn)
    with client.session_transaction() as s:
        s['nome_utente'] = 'reg_admin'
        s['is_admin'] = True
    r = client.get('/admin')
    assert r.status_code == 200


def test_attiva_giornata_sqlite(client):
    """Prima falliva su SQLite (ON CONFLICT solo Postgres)."""
    _crea_utente('reg_admin2', is_admin=True)
    with client.session_transaction() as s:
        s['nome_utente'] = 'reg_admin2'
        s['is_admin'] = True
    r = client.post('/attiva-giornata', data={'giornata': '38'})
    assert r.status_code == 302
    with db_conn() as conn:
        row = db_fetchone(conn,
                          'SELECT is_attiva FROM stato_giornata WHERE giornata = 38')
    assert row is not None and row_get(row, 'is_attiva')


def test_pagina_404_personalizzata(client):
    r = client.get('/questa-rotta-non-esiste')
    assert r.status_code == 404
    assert 'Pagina non trovata' in r.data.decode('utf-8')


def test_admin_required_blocca_non_admin(client):
    with client.session_transaction() as s:
        s['nome_utente'] = 'tizio_non_admin'
        s['is_admin'] = False
    r = client.get('/admin')
    assert r.status_code == 403
