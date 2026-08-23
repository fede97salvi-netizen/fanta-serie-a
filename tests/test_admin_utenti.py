"""Test per la pagina admin di gestione utenti: deve mostrare chi non ha
mai pronosticato, così l'admin può individuarli ed eliminarli."""

from tests.conftest import _crea_utente
from db_utils import db_conn, db_execute, db_commit


def _crea_partita_e_pronostico(conn, id_utente, giornata):
    db_execute(
        conn,
        'INSERT INTO partite (giornata, squadra_casa, squadra_ospite, pronosticabile) '
        'VALUES (?, ?, ?, ?)',
        (giornata, 'Casa', 'Ospite', True),
    )
    db_commit(conn)
    from db_utils import db_fetchone, row_get
    pid = row_get(db_fetchone(conn, 'SELECT id FROM partite ORDER BY id DESC LIMIT 1'), 'id')
    db_execute(
        conn,
        'INSERT INTO pronostici_giornata (id_utente, id_partita, esito_pronosticato) '
        'VALUES (?, ?, ?)',
        (id_utente, pid, '1'),
    )
    db_commit(conn)


def test_admin_utenti_segnala_chi_non_ha_mai_pronosticato(client):
    uid_attivo = _crea_utente('admin_utenti_attivo')
    _crea_utente('admin_utenti_inattivo')
    _crea_utente('admin_utenti_admin', is_admin=True)

    with db_conn() as conn:
        _crea_partita_e_pronostico(conn, uid_attivo, giornata=1)

    with client.session_transaction() as sess:
        sess['nome_utente'] = 'admin_utenti_admin'
        sess['is_admin'] = True

    r = client.get('/admin/utenti')
    assert r.status_code == 200
    html = r.get_data(as_text=True)

    assert 'admin_utenti_attivo' in html
    assert 'admin_utenti_inattivo' in html
    assert 'mai attivo' in html
    assert '1 pronostico' in html
