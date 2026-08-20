"""Test per invia_notifiche.invia_promemoria_partite (promemoria 30' prima)."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import _crea_utente
from db_utils import db_conn, db_execute, db_commit, db_fetchone, row_get


def _crea_partita(conn, giornata, ore_al_calcio_dinizio, pronosticabile=True):
    orario = (datetime.now(timezone.utc) + timedelta(minutes=ore_al_calcio_dinizio)) \
        .strftime('%Y-%m-%dT%H:%M:%SZ')
    db_execute(
        conn,
        'INSERT INTO partite (giornata, squadra_casa, squadra_ospite, '
        'pronosticabile, data_ora_partita) VALUES (?, ?, ?, ?, ?)',
        (giornata, 'Squadra Casa', 'Squadra Ospite', pronosticabile, orario),
    )
    db_commit(conn)
    return row_get(
        db_fetchone(conn, 'SELECT id FROM partite ORDER BY id DESC LIMIT 1'), 'id')


def _iscrivi_push(conn, id_utente):
    sub = json.dumps({'endpoint': f'https://example.test/{id_utente}',
                      'keys': {'auth': 'a', 'p256dh': 'p'}})
    db_execute(
        conn,
        'INSERT INTO push_subscriptions (id_utente, subscription_info, nome_utente) '
        'VALUES (?, ?, ?)',
        (id_utente, sub, f'utente{id_utente}'),
    )
    db_commit(conn)


@pytest.fixture(autouse=True)
def _vapid_env(monkeypatch):
    monkeypatch.setenv('VAPID_PRIVATE_KEY', 'chiave-finta')


def test_invia_solo_a_chi_non_ha_pronosticato(app, monkeypatch):
    inviate = []

    def _fake_webpush(subscription_info, data, vapid_private_key, vapid_claims):
        inviate.append(subscription_info['endpoint'])

    monkeypatch.setattr('invia_notifiche.webpush', _fake_webpush)

    with db_conn() as conn:
        uid_senza_pronostico = _crea_utente('senza_pronostico')
        uid_con_pronostico = _crea_utente('con_pronostico')
        pid = _crea_partita(conn, giornata=1, ore_al_calcio_dinizio=25)
        _iscrivi_push(conn, uid_senza_pronostico)
        _iscrivi_push(conn, uid_con_pronostico)
        db_execute(
            conn,
            'INSERT INTO pronostici_giornata (id_utente, id_partita, esito_pronosticato) '
            'VALUES (?, ?, ?)',
            (uid_con_pronostico, pid, '1'),
        )
        db_commit(conn)

    from invia_notifiche import invia_promemoria_partite
    esito = invia_promemoria_partite()

    assert f'https://example.test/{uid_senza_pronostico}' in inviate
    assert f'https://example.test/{uid_con_pronostico}' not in inviate
    assert 'Notifiche inviate: 1' in esito

    with db_conn() as conn:
        partita = db_fetchone(conn, 'SELECT promemoria_inviato FROM partite WHERE id = ?', (pid,))
    assert row_get(partita, 'promemoria_inviato') in (1, True)


def test_ignora_partite_fuori_dalla_finestra(app, monkeypatch):
    inviate = []
    monkeypatch.setattr('invia_notifiche.webpush',
                        lambda **kw: inviate.append(kw['subscription_info']['endpoint']))

    with db_conn() as conn:
        uid = _crea_utente('utente_lontano')
        _crea_partita(conn, giornata=2, ore_al_calcio_dinizio=180)  # 3 ore: fuori finestra
        _iscrivi_push(conn, uid)

    from invia_notifiche import invia_promemoria_partite
    invia_promemoria_partite()

    assert not inviate


def test_non_reinvia_a_partita_gia_avvisata(app, monkeypatch):
    chiamate = []
    monkeypatch.setattr(
        'invia_notifiche.webpush',
        lambda **kw: chiamate.append(kw['subscription_info']['endpoint']))

    with db_conn() as conn:
        uid = _crea_utente('utente_doppio_giro')
        _crea_partita(conn, giornata=3, ore_al_calcio_dinizio=20)
        _iscrivi_push(conn, uid)

    endpoint_mio = f'https://example.test/{uid}'

    from invia_notifiche import invia_promemoria_partite
    invia_promemoria_partite()
    assert chiamate.count(endpoint_mio) == 1

    invia_promemoria_partite()  # secondo giro dello scheduler nella stessa finestra
    assert chiamate.count(endpoint_mio) == 1  # nessun doppione verso di me
