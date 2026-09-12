"""Test per invia_notifiche.invia_promemoria_scadenza (alert 5' prima,
solo a chi non ha ancora inserito il pronostico)."""

import pytest

from tests.conftest import _crea_utente
from tests.test_promemoria_partite import _crea_partita, _iscrivi_push
from db_utils import db_conn, db_execute, db_commit, db_fetchone, row_get


@pytest.fixture(autouse=True)
def _vapid_env(monkeypatch):
    monkeypatch.setenv('VAPID_PRIVATE_KEY', 'chiave-finta')


def test_avvisa_solo_chi_non_ha_pronosticato(app, monkeypatch):
    # Cattura anche il messaggio (non solo l'endpoint): il DB di test è
    # condiviso tra tutti i moduli della sessione pytest, quindi possono
    # esserci altre partite/iscritti in finestra allo stesso momento create
    # da altri test. Usando nomi squadra unici per questa partita possiamo
    # isolare le notifiche che riguardano *questa* partita da quelle altrui.
    inviate = []
    monkeypatch.setattr(
        'invia_notifiche.webpush',
        lambda subscription_info, data, **kw:
            inviate.append((subscription_info['endpoint'], data)))

    with db_conn() as conn:
        uid_senza_pronostico = _crea_utente('senza_pronostico_5m')
        uid_con_pronostico = _crea_utente('con_pronostico_5m')
        pid = _crea_partita(conn, giornata=1, ore_al_calcio_dinizio=5)
        db_execute(conn, "UPDATE partite SET squadra_casa = 'CasaTestScadenza', "
                         "squadra_ospite = 'OspiteTestScadenza' WHERE id = ?", (pid,))
        _iscrivi_push(conn, uid_senza_pronostico)
        _iscrivi_push(conn, uid_con_pronostico)
        db_execute(
            conn,
            'INSERT INTO pronostici_giornata (id_utente, id_partita, esito_pronosticato) '
            'VALUES (?, ?, ?)',
            (uid_con_pronostico, pid, '1'),
        )
        db_commit(conn)

    from invia_notifiche import invia_promemoria_scadenza
    invia_promemoria_scadenza()

    per_questa_partita = [ep for ep, data in inviate if 'CasaTestScadenza' in data]
    assert f'https://example.test/{uid_senza_pronostico}' in per_questa_partita
    assert f'https://example.test/{uid_con_pronostico}' not in per_questa_partita

    with db_conn() as conn:
        partita = db_fetchone(
            conn, 'SELECT promemoria_scadenza_inviato FROM partite WHERE id = ?', (pid,))
    assert row_get(partita, 'promemoria_scadenza_inviato') in (1, True)


def test_ignora_partite_fuori_dalla_finestra(app, monkeypatch):
    inviate = []
    monkeypatch.setattr(
        'invia_notifiche.webpush',
        lambda **kw: inviate.append(kw['subscription_info']['endpoint']))

    with db_conn() as conn:
        uid = _crea_utente('utente_lontano_5m')
        _crea_partita(conn, giornata=2, ore_al_calcio_dinizio=25)  # dentro la finestra dei 30', fuori da quella dei 5'
        _iscrivi_push(conn, uid)

    from invia_notifiche import invia_promemoria_scadenza
    invia_promemoria_scadenza()

    assert not inviate


def test_non_reinvia_a_partita_gia_avvisata(app, monkeypatch):
    chiamate = []
    monkeypatch.setattr(
        'invia_notifiche.webpush',
        lambda **kw: chiamate.append(kw['subscription_info']['endpoint']))

    with db_conn() as conn:
        uid = _crea_utente('utente_doppio_giro_5m')
        _crea_partita(conn, giornata=3, ore_al_calcio_dinizio=3)
        _iscrivi_push(conn, uid)

    endpoint_mio = f'https://example.test/{uid}'

    from invia_notifiche import invia_promemoria_scadenza
    invia_promemoria_scadenza()
    assert chiamate.count(endpoint_mio) == 1

    invia_promemoria_scadenza()  # secondo giro dello scheduler nella stessa finestra
    assert chiamate.count(endpoint_mio) == 1  # nessun doppione verso di me


def test_non_interferisce_con_il_promemoria_dei_30_minuti(app, monkeypatch):
    """Le due finestre (30' e 5') e i due flag sono indipendenti: inviare
    l'uno non deve marcare come già inviato anche l'altro."""
    monkeypatch.setattr('invia_notifiche.webpush', lambda **kw: None)

    with db_conn() as conn:
        uid = _crea_utente('utente_entrambi_gli_alert')
        pid = _crea_partita(conn, giornata=4, ore_al_calcio_dinizio=5)
        _iscrivi_push(conn, uid)

    from invia_notifiche import invia_promemoria_scadenza
    invia_promemoria_scadenza()

    with db_conn() as conn:
        partita = db_fetchone(
            conn, 'SELECT promemoria_inviato, promemoria_scadenza_inviato '
                  'FROM partite WHERE id = ?', (pid,))
    assert row_get(partita, 'promemoria_scadenza_inviato') in (1, True)
    assert row_get(partita, 'promemoria_inviato') in (0, False, None)
