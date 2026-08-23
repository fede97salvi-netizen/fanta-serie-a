"""Test per /admin/rinomina-utente: l'admin può cambiare il nome utente di
un giocatore."""

from tests.conftest import _crea_utente
from db_utils import db_conn, db_fetchone, row_get


def _login_admin(client):
    _crea_utente('admin_rinomina', is_admin=True)
    with client.session_transaction() as sess:
        sess['nome_utente'] = 'admin_rinomina'
        sess['is_admin'] = True


def test_rinomina_utente_ok(client):
    uid = _crea_utente('vecchio_nome')
    _login_admin(client)

    r = client.post(f'/admin/rinomina-utente/{uid}',
                    data={'nuovo_nome_utente': 'nuovo_nome'},
                    follow_redirects=True)
    assert r.status_code == 200

    with db_conn() as conn:
        row = db_fetchone(conn, 'SELECT nome_utente FROM utenti WHERE id = ?', (uid,))
    assert row_get(row, 'nome_utente') == 'nuovo_nome'


def test_rinomina_utente_nome_gia_in_uso(client):
    uid = _crea_utente('utente_a_rinominare')
    _crea_utente('nome_gia_preso')
    _login_admin(client)

    r = client.post(f'/admin/rinomina-utente/{uid}',
                    data={'nuovo_nome_utente': 'nome_gia_preso'},
                    follow_redirects=True)
    assert r.status_code == 200

    with db_conn() as conn:
        row = db_fetchone(conn, 'SELECT nome_utente FROM utenti WHERE id = ?', (uid,))
    assert row_get(row, 'nome_utente') == 'utente_a_rinominare'  # invariato


def test_rinomina_utente_nome_troppo_corto(client):
    uid = _crea_utente('utente_nome_corto')
    _login_admin(client)

    r = client.post(f'/admin/rinomina-utente/{uid}',
                    data={'nuovo_nome_utente': 'a'},
                    follow_redirects=True)
    assert r.status_code == 200

    with db_conn() as conn:
        row = db_fetchone(conn, 'SELECT nome_utente FROM utenti WHERE id = ?', (uid,))
    assert row_get(row, 'nome_utente') == 'utente_nome_corto'  # invariato
