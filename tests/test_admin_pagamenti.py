"""Test per la sezione admin Pagamenti: elenco utenti con stato pagamento
e aggiornamento (segna pagato/non pagato, importo, data, note)."""

from tests.conftest import _crea_utente
from db_utils import db_conn, db_fetchone, row_get


def _login_admin(client):
    _crea_utente('admin_pagamenti', is_admin=True)
    with client.session_transaction() as sess:
        sess['nome_utente'] = 'admin_pagamenti'
        sess['is_admin'] = True


def test_utente_senza_riga_pagamenti_risulta_non_pagato(client):
    _crea_utente('utente_pagamenti_nuovo')
    _login_admin(client)

    r = client.get('/admin/pagamenti')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'utente_pagamenti_nuovo' in html


def test_segna_come_pagato_con_importo_e_nota(client):
    uid = _crea_utente('utente_da_pagare')
    _login_admin(client)

    r = client.post(f'/admin/pagamenti/{uid}/aggiorna', data={
        'ha_pagato': '1',
        'importo': '25',
        'data_pagamento': '06/09/2026',
        'note': 'Bonifico',
    }, follow_redirects=True)
    assert r.status_code == 200

    with db_conn() as conn:
        row = db_fetchone(conn, 'SELECT * FROM pagamenti WHERE id_utente = ?', (uid,))
    assert row_get(row, 'ha_pagato') in (1, True)
    assert row_get(row, 'importo') == 25.0
    assert row_get(row, 'data_pagamento') == '06/09/2026'
    assert row_get(row, 'note') == 'Bonifico'


def test_aggiornamento_successivo_sovrascrive_riga_esistente(client):
    uid = _crea_utente('utente_pagamento_modificato')
    _login_admin(client)

    client.post(f'/admin/pagamenti/{uid}/aggiorna', data={
        'ha_pagato': '1', 'importo': '25', 'data_pagamento': '', 'note': '',
    })
    client.post(f'/admin/pagamenti/{uid}/aggiorna', data={
        'ha_pagato': '', 'importo': '', 'data_pagamento': '', 'note': 'rimborsato',
    })

    with db_conn() as conn:
        row = db_fetchone(conn, 'SELECT * FROM pagamenti WHERE id_utente = ?', (uid,))
    assert row_get(row, 'ha_pagato') in (0, False)
    assert row_get(row, 'importo') is None
    assert row_get(row, 'note') == 'rimborsato'


def test_importo_non_numerico_viene_rifiutato(client):
    uid = _crea_utente('utente_importo_sbagliato')
    _login_admin(client)

    r = client.post(f'/admin/pagamenti/{uid}/aggiorna', data={
        'ha_pagato': '1', 'importo': 'abc', 'data_pagamento': '', 'note': '',
    }, follow_redirects=True)
    assert r.status_code == 200

    with db_conn() as conn:
        row = db_fetchone(conn, 'SELECT * FROM pagamenti WHERE id_utente = ?', (uid,))
    assert row is None  # nessuna riga scritta, l'importo non valido ha bloccato il salvataggio
