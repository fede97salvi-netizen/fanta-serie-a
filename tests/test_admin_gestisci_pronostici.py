"""Test per /admin/gestisci-pronostici/<giornata>: l'admin deve poter
inserire o correggere retroattivamente il pronostico di un utente, anche
per una partita già iniziata/scaduta, ed eliminarne uno esistente."""

from datetime import datetime, timedelta, timezone

from tests.conftest import _crea_utente
from db_utils import db_conn, db_execute, db_commit, db_fetchone, row_get


def _login_admin(client):
    _crea_utente('admin_pronostici', is_admin=True)
    with client.session_transaction() as sess:
        sess['nome_utente'] = 'admin_pronostici'
        sess['is_admin'] = True


def _crea_partita_scaduta(conn, giornata):
    orario_passato = (datetime.now(timezone.utc) - timedelta(hours=3)) \
        .strftime('%Y-%m-%dT%H:%M:%SZ')
    db_execute(
        conn,
        'INSERT INTO partite (giornata, squadra_casa, squadra_ospite, '
        'pronosticabile, data_ora_partita) VALUES (?, ?, ?, ?, ?)',
        (giornata, 'Casa', 'Ospite', True, orario_passato),
    )
    db_commit(conn)
    return row_get(db_fetchone(conn, 'SELECT id FROM partite ORDER BY id DESC LIMIT 1'), 'id')


def test_admin_inserisce_pronostico_per_partita_gia_scaduta(client):
    uid = _crea_utente('utente_dimenticato')
    _login_admin(client)
    with db_conn() as conn:
        pid = _crea_partita_scaduta(conn, giornata=1)

    r = client.post('/admin/gestisci-pronostici/1', data={
        'action': 'salva', 'id_partita': str(pid), 'id_utente': str(uid),
        'esito': '1', 'risultato_casa': '2', 'risultato_ospite': '0',
        'marcatore': 'Nessun Marcatore',
    }, follow_redirects=True)
    assert r.status_code == 200

    with db_conn() as conn:
        row = db_fetchone(conn, 'SELECT * FROM pronostici_giornata WHERE id_utente = ? AND id_partita = ?',
                          (uid, pid))
    assert row is not None
    assert row_get(row, 'esito_pronosticato') == '1'
    assert row_get(row, 'risultato_casa_pronosticato') == 2
    assert row_get(row, 'risultato_ospite_pronosticato') == 0


def test_admin_corregge_pronostico_esistente(client):
    uid = _crea_utente('utente_da_correggere')
    _login_admin(client)
    with db_conn() as conn:
        pid = _crea_partita_scaduta(conn, giornata=2)
        db_execute(conn,
                   'INSERT INTO pronostici_giornata (id_utente, id_partita, esito_pronosticato) '
                   'VALUES (?, ?, ?)', (uid, pid, 'X'))
        db_commit(conn)

    client.post('/admin/gestisci-pronostici/2', data={
        'action': 'salva', 'id_partita': str(pid), 'id_utente': str(uid),
        'esito': '2', 'risultato_casa': '0', 'risultato_ospite': '3',
        'marcatore': '',
    })

    with db_conn() as conn:
        row = db_fetchone(conn, 'SELECT * FROM pronostici_giornata WHERE id_utente = ? AND id_partita = ?',
                          (uid, pid))
    assert row_get(row, 'esito_pronosticato') == '2'
    assert row_get(row, 'risultato_ospite_pronosticato') == 3


def test_admin_elimina_pronostico(client):
    uid = _crea_utente('utente_da_rimuovere')
    _login_admin(client)
    with db_conn() as conn:
        pid = _crea_partita_scaduta(conn, giornata=3)
        db_execute(conn,
                   'INSERT INTO pronostici_giornata (id_utente, id_partita, esito_pronosticato) '
                   'VALUES (?, ?, ?)', (uid, pid, '1'))
        db_commit(conn)
        id_pronostico = row_get(
            db_fetchone(conn, 'SELECT id FROM pronostici_giornata WHERE id_utente = ? AND id_partita = ?',
                       (uid, pid)), 'id')

    client.post('/admin/gestisci-pronostici/3', data={
        'action': 'cancella', 'id_pronostico': str(id_pronostico),
    })

    with db_conn() as conn:
        row = db_fetchone(conn, 'SELECT * FROM pronostici_giornata WHERE id = ?', (id_pronostico,))
    assert row is None


def test_pagina_mostra_form_e_utenti(client):
    _crea_utente('utente_visibile_in_select')
    _login_admin(client)
    with db_conn() as conn:
        _crea_partita_scaduta(conn, giornata=4)

    r = client.get('/admin/gestisci-pronostici/4')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'utente_visibile_in_select' in html
    assert 'Inserisci o correggi un pronostico' in html
