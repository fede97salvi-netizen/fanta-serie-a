"""Test degli interventi di fine settembre 2026: rotte di test protette,
registrazione su invito, blocco del login, push multi-dispositivo,
bonus di fine stagione, ricalcolo della classifica, installazione PWA,
sessione di un utente rinominato."""

import json
import os
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pywebpush import WebPushException

from tests.conftest import _crea_utente
from db_utils import db_conn, db_execute, db_fetchone, db_fetchall, db_commit, row_get
from extensions import limiter

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def _reset_limiter():
    limiter.reset()
    yield
    limiter.reset()


def _login(client, nome, admin=False):
    with client.session_transaction() as s:
        s['nome_utente'] = nome
        s['is_admin'] = admin


def _pulisci_push(*endpoint):
    with db_conn() as conn:
        for e in endpoint:
            db_execute(conn, 'DELETE FROM push_subscriptions WHERE endpoint = ?', (e,))
        db_commit(conn)


# ─── Rotte di test delle notifiche ───────────────────────────────────────────

@pytest.mark.parametrize('url', ['/test_spara_notifica', '/test_promemoria_partite',
                                 '/test_promemoria_scadenza'])
def test_rotte_test_notifiche_solo_admin(client, url, monkeypatch):
    chiamate = []
    monkeypatch.setattr('app.invia_promemoria_generale',
                        lambda titolo, msg: chiamate.append(titolo) or 'ok')
    assert client.get(url).status_code == 403
    _crea_utente('sic_normale')
    _login(client, 'sic_normale')
    assert client.get(url).status_code == 403
    assert chiamate == []
    _crea_utente('sic_admin', is_admin=True)
    _login(client, 'sic_admin', admin=True)
    assert client.get(url).status_code == 200
    assert len(chiamate) == 1


# ─── Iscrizione push ─────────────────────────────────────────────────────────

def test_salva_iscrizione_push_richiede_login(client):
    r = client.post('/salva_iscrizione_push',
                    json={'endpoint': 'https://push.test/anonimo', 'keys': {}})
    assert r.status_code == 401
    with db_conn() as conn:
        assert db_fetchone(conn, 'SELECT id FROM push_subscriptions WHERE endpoint = ?',
                           ('https://push.test/anonimo',)) is None


def test_salva_iscrizione_push_da_loggato(client):
    uid = _crea_utente('sic_push_web')
    _login(client, 'sic_push_web')
    r = client.post('/salva_iscrizione_push',
                    json={'endpoint': 'https://push.test/web-1', 'keys': {'auth': 'a', 'p256dh': 'p'}})
    assert r.status_code == 200 and r.get_json()['status'] == 'success'
    with db_conn() as conn:
        riga = db_fetchone(conn, 'SELECT id_utente FROM push_subscriptions WHERE endpoint = ?',
                           ('https://push.test/web-1',))
    assert row_get(riga, 'id_utente') == uid
    assert client.post('/salva_iscrizione_push', json={'keys': {}}).status_code == 400
    _pulisci_push('https://push.test/web-1')


def test_piu_dispositivi_per_utente(app):
    from invia_notifiche import salva_subscription_push
    uid = _crea_utente('sic_multi')
    telefono = {'endpoint': 'https://push.test/multi-telefono', 'keys': {'auth': 'a', 'p256dh': 'p'}}
    pc = {'endpoint': 'https://push.test/multi-pc', 'keys': {'auth': 'a', 'p256dh': 'p'}}
    assert salva_subscription_push('sic_multi', telefono)
    assert salva_subscription_push('sic_multi', pc)
    assert salva_subscription_push('sic_multi', telefono)   # di nuovo: nessun doppione
    with db_conn() as conn:
        righe = db_fetchall(conn, 'SELECT endpoint FROM push_subscriptions WHERE id_utente = ?', (uid,))
    assert sorted(row_get(r, 'endpoint') for r in righe) == [pc['endpoint'], telefono['endpoint']]
    _pulisci_push(telefono['endpoint'], pc['endpoint'])


def test_dispositivo_passa_al_nuovo_utente(app):
    from invia_notifiche import salva_subscription_push
    _crea_utente('sic_disp_a')
    uid_b = _crea_utente('sic_disp_b')
    sub = {'endpoint': 'https://push.test/condiviso', 'keys': {'auth': 'a', 'p256dh': 'p'}}
    salva_subscription_push('sic_disp_a', sub)
    salva_subscription_push('sic_disp_b', sub)
    with db_conn() as conn:
        righe = db_fetchall(conn, 'SELECT id_utente FROM push_subscriptions WHERE endpoint = ?',
                            (sub['endpoint'],))
    assert [row_get(r, 'id_utente') for r in righe] == [uid_b]
    assert salva_subscription_push('nessuno_xyz', sub) is False
    _pulisci_push(sub['endpoint'])


def test_iscrizione_scaduta_rimuove_solo_quel_dispositivo(app, monkeypatch):
    from invia_notifiche import salva_subscription_push, invia_promemoria_generale
    monkeypatch.setenv('VAPID_PRIVATE_KEY', 'chiave-finta')
    _crea_utente('sic_scaduta')
    vivo = {'endpoint': 'https://push.test/scad-vivo', 'keys': {'auth': 'a', 'p256dh': 'p'}}
    morto = {'endpoint': 'https://push.test/scad-morto', 'keys': {'auth': 'a', 'p256dh': 'p'}}
    salva_subscription_push('sic_scaduta', vivo)
    salva_subscription_push('sic_scaduta', morto)

    def _fake_webpush(subscription_info, data, vapid_private_key, vapid_claims):
        if subscription_info['endpoint'] == morto['endpoint']:
            raise WebPushException('gone', response=SimpleNamespace(status_code=410, text=''))

    monkeypatch.setattr('invia_notifiche.webpush', _fake_webpush)
    invia_promemoria_generale('Titolo', 'Messaggio')
    with db_conn() as conn:
        rimasti = {row_get(r, 'endpoint') for r in db_fetchall(
            conn, 'SELECT endpoint FROM push_subscriptions WHERE endpoint IN (?, ?)',
            (vivo['endpoint'], morto['endpoint']))}
    assert rimasti == {vivo['endpoint']}
    _pulisci_push(vivo['endpoint'])


def test_prova_notifiche_solo_sui_dispositivi_dell_admin(client, monkeypatch):
    from invia_notifiche import salva_subscription_push
    monkeypatch.setenv('VAPID_PRIVATE_KEY', 'chiave-finta')
    inviate = []
    monkeypatch.setattr('invia_notifiche.webpush',
                        lambda **kw: inviate.append(kw['subscription_info']['endpoint']))
    _crea_utente('sic_admin_prova', is_admin=True)
    _crea_utente('sic_altro_prova')
    miei = ['https://push.test/prova-tel', 'https://push.test/prova-pc']
    altro = 'https://push.test/prova-altro'
    for e in miei:
        salva_subscription_push('sic_admin_prova', {'endpoint': e, 'keys': {}})
    salva_subscription_push('sic_altro_prova', {'endpoint': altro, 'keys': {}})

    _login(client, 'sic_admin_prova', admin=True)
    assert 'Prova notifiche' in client.get('/admin').data.decode('utf-8')
    html = client.post('/admin/prova-notifiche', follow_redirects=True).data.decode('utf-8')
    assert sorted(inviate) == sorted(miei)          # l'altro utente non riceve nulla
    assert 'inviata a 2' in html
    _pulisci_push(*miei, altro)


def test_prova_notifiche_senza_dispositivi_e_non_admin(client, monkeypatch):
    monkeypatch.setenv('VAPID_PRIVATE_KEY', 'chiave-finta')
    monkeypatch.setattr('invia_notifiche.webpush', lambda **kw: None)
    _crea_utente('sic_admin_senza', is_admin=True)
    _login(client, 'sic_admin_senza', admin=True)
    html = client.post('/admin/prova-notifiche', follow_redirects=True).data.decode('utf-8')
    assert 'Nessun tuo dispositivo' in html
    _crea_utente('sic_normale_prova')
    _login(client, 'sic_normale_prova')
    assert client.post('/admin/prova-notifiche').status_code == 403


# ─── Etichetta (tag) delle notifiche ─────────────────────────────────────────

G_TAG = 96


def test_notifiche_di_partite_diverse_non_si_sovrascrivono(app, monkeypatch):
    """Due partite alla stessa ora: tag diversi, cosi' sul telefono restano
    visibili entrambi i promemoria. Stessa partita: stesso tag per il
    "manca mezz'ora" e per l'alert "ultimi minuti"."""
    from invia_notifiche import (salva_subscription_push, invia_promemoria_partite,
                                 invia_promemoria_scadenza)
    monkeypatch.setenv('VAPID_PRIVATE_KEY', 'chiave-finta')
    payload = []
    monkeypatch.setattr('invia_notifiche.webpush',
                        lambda **kw: payload.append(json.loads(kw['data'])))
    _crea_utente('sic_tag')
    salva_subscription_push('sic_tag', {'endpoint': 'https://push.test/tag', 'keys': {}})

    def _partita(minuti):
        orario = (datetime.now(timezone.utc) + timedelta(minutes=minuti)).strftime('%Y-%m-%dT%H:%M:%SZ')
        with db_conn() as conn:
            db_execute(conn, 'INSERT INTO partite (giornata, squadra_casa, squadra_ospite, '
                             'pronosticabile, data_ora_partita) VALUES (?, ?, ?, 1, ?)',
                       (G_TAG, 'TAG CASA', 'TAG OSPITE', orario))
            db_commit(conn)
            return row_get(db_fetchone(conn, 'SELECT id FROM partite ORDER BY id DESC LIMIT 1'), 'id')

    try:
        pid_a, pid_b = _partita(25), _partita(25)
        invia_promemoria_partite()
        mezzora = {p['tag']: p for p in payload if p['tag'] in (f'partita-{pid_a}', f'partita-{pid_b}')}
        assert set(mezzora) == {f'partita-{pid_a}', f'partita-{pid_b}'}
        assert mezzora[f'partita-{pid_a}']['url'] == f'/pronostici-giornata/{G_TAG}'

        payload.clear()
        pid_c = _partita(5)
        invia_promemoria_scadenza()
        assert f'partita-{pid_c}' in {p['tag'] for p in payload}
    finally:
        _pulisci_push('https://push.test/tag')
        _pulisci_giornate(G_TAG)


def test_service_worker_senza_tag_fisso(client):
    sw = client.get('/sw.js').data.decode('utf-8')
    assert 'fantaseriea-notification' not in sw          # niente piu' tag unico per tutte
    assert 'if (data.tag)' in sw and 'renotify = true' in sw
    assert 'new URL(' in sw                              # confronto tra indirizzi completi


# ─── Registrazione su invito ─────────────────────────────────────────────────

def test_registrazione_senza_invito_non_mostra_il_form(client):
    html = client.get('/registrazione').data.decode('utf-8')
    assert "link d'invito" in html
    assert 'name="password"' not in html
    html = client.get('/registrazione?invito=sbagliato').data.decode('utf-8')
    assert 'name="password"' not in html


@pytest.mark.parametrize('codice', ['', 'codice-sbagliato'])
def test_registrazione_senza_invito_valido_non_crea_utente(client, codice):
    client.post('/registrazione', data={'nome_utente': 'sic_intruso', 'password': 'pass123',
                                        'codice_invito': codice})
    with db_conn() as conn:
        assert db_fetchone(conn, "SELECT id FROM utenti WHERE nome_utente = 'sic_intruso'") is None


def test_registrazione_con_invito_e_rigenerazione(client):
    from services.inviti import leggi_codice_invito
    vecchio = leggi_codice_invito()
    html = client.get(f'/registrazione?invito={vecchio}').data.decode('utf-8')
    assert 'name="password"' in html and vecchio in html
    r = client.post('/registrazione', data={'nome_utente': 'sic_invitato', 'password': 'pass123',
                                            'codice_invito': vecchio})
    assert r.status_code == 302

    _crea_utente('sic_admin_inviti', is_admin=True)
    _login(client, 'sic_admin_inviti', admin=True)
    assert vecchio in client.get('/admin/utenti').data.decode('utf-8')
    client.post('/admin/rigenera-invito')
    nuovo = leggi_codice_invito()
    assert nuovo and nuovo != vecchio

    client2 = client.application.test_client()
    client2.post('/registrazione', data={'nome_utente': 'sic_vecchio_link', 'password': 'pass123',
                                         'codice_invito': vecchio})
    with db_conn() as conn:
        assert db_fetchone(conn, "SELECT id FROM utenti WHERE nome_utente = 'sic_vecchio_link'") is None
        assert db_fetchone(conn, "SELECT id FROM utenti WHERE nome_utente = 'sic_invitato'") is not None


# ─── Blocco del login dopo 10 tentativi ──────────────────────────────────────

def _prova_login(client, nome, password):
    limiter.reset()   # il limite per IP (5/min) qui non interessa
    return client.post('/login', data={'nome_utente': nome, 'password': password})


def test_login_bloccato_dopo_dieci_tentativi(client):
    from blueprints.auth import MAX_TENTATIVI_LOGIN
    _crea_utente('sic_blocco', 'giusta123')
    for _ in range(MAX_TENTATIVI_LOGIN - 1):
        r = _prova_login(client, 'sic_blocco', 'sbagliata')
        assert 'Credenziali non valide' in r.data.decode('utf-8')
    r = _prova_login(client, 'sic_blocco', 'sbagliata')
    assert 'bloccato' in r.data.decode('utf-8')
    # Anche con la password giusta resta bloccato finche' non scade il blocco
    r = _prova_login(client, 'sic_blocco', 'giusta123')
    assert r.status_code == 200 and 'bloccato' in r.data.decode('utf-8')

    passato = (datetime.now(timezone.utc).replace(tzinfo=None)
               - timedelta(minutes=1)).isoformat(timespec='seconds')
    with db_conn() as conn:
        db_execute(conn, "UPDATE utenti SET bloccato_fino = ? WHERE nome_utente = 'sic_blocco'",
                   (passato,))
        db_commit(conn)
    r = _prova_login(client, 'sic_blocco', 'giusta123')
    assert r.status_code == 302
    with db_conn() as conn:
        u = db_fetchone(conn, "SELECT tentativi_falliti, bloccato_fino FROM utenti "
                              "WHERE nome_utente = 'sic_blocco'")
    assert row_get(u, 'tentativi_falliti') == 0 and row_get(u, 'bloccato_fino') is None


def test_login_riuscito_azzera_i_tentativi(client):
    _crea_utente('sic_azzera', 'giusta123')
    for _ in range(3):
        _prova_login(client, 'sic_azzera', 'sbagliata')
    assert _prova_login(client, 'sic_azzera', 'giusta123').status_code == 302
    with db_conn() as conn:
        u = db_fetchone(conn, "SELECT tentativi_falliti FROM utenti WHERE nome_utente = 'sic_azzera'")
    assert row_get(u, 'tentativi_falliti') == 0


# ─── Bonus di fine stagione e ricalcolo ──────────────────────────────────────

G_BONUS = 93
G_NON_ARCHIVIATA = 94


def _totale(uid):
    with db_conn() as conn:
        return row_get(db_fetchone(conn, 'SELECT punteggio_totale FROM punteggi WHERE id_utente = ?',
                                   (uid,)), 'punteggio_totale')


def _partita_con_pronostico(giornata, uid, casa, osp, esito, pr_casa, pr_osp):
    with db_conn() as conn:
        db_execute(conn, 'INSERT INTO partite (giornata, squadra_casa, squadra_ospite, '
                         'risultato_casa_reale, risultato_ospite_reale, pronosticabile) '
                         'VALUES (?, ?, ?, ?, ?, 1)', (giornata, 'SIC CASA', 'SIC OSPITE', casa, osp))
        pid = row_get(db_fetchone(conn, 'SELECT id FROM partite ORDER BY id DESC LIMIT 1'), 'id')
        db_execute(conn, 'INSERT INTO pronostici_giornata (id_utente, id_partita, esito_pronosticato, '
                         'risultato_casa_pronosticato, risultato_ospite_pronosticato) '
                         'VALUES (?, ?, ?, ?, ?)', (uid, pid, esito, pr_casa, pr_osp))
        db_commit(conn)


def _pulisci_giornate(*giornate):
    from services.game_logic import ricalcola_punteggi_totali
    with db_conn() as conn:
        for g in giornate:
            db_execute(conn, 'DELETE FROM pronostici_giornata WHERE id_partita IN '
                             '(SELECT id FROM partite WHERE giornata = ?)', (g,))
            db_execute(conn, 'DELETE FROM partite WHERE giornata = ?', (g,))
        db_commit(conn)
    ricalcola_punteggi_totali()


def test_bonus_finale_sopravvive_ai_ricalcoli(app):
    from services.game_logic import (ricalcola_punteggi_finali, ricalcola_punteggi_totali,
                                     calcola_e_aggiorna_punti_giornata)
    uid = _crea_utente('sic_bonus')
    with db_conn() as conn:
        db_execute(conn, 'INSERT INTO pronostici_iniziali (id_utente, squadra_1, squadra_2, '
                         'squadra_3, squadra_4, capocannoniere) VALUES (?, ?, ?, ?, ?, ?)',
                   (uid, 'SIC A', 'SIC B', 'SIC C', 'SIC D', 'Bomber Test'))
        db_execute(conn, 'UPDATE risultati_finali SET squadra_1 = ?, squadra_2 = ?, squadra_3 = ?, '
                         'squadra_4 = ?, capocannoniere = ? WHERE id = 1',
                   ('SIC A', 'SIC C', 'SIC B', 'SIC X', 'Bomber Test'))
        db_commit(conn)
    try:
        ricalcola_punteggi_finali()
        # A esatta (5+10), B e C tra le prime 4 (5+5), D no, capocannoniere (15)
        assert _totale(uid) == 40
        ricalcola_punteggi_finali()
        assert _totale(uid) == 40          # ripetere il calcolo non raddoppia
        ricalcola_punteggi_totali()
        assert _totale(uid) == 40          # "Ricalcola tutto" non cancella il bonus
        _partita_con_pronostico(G_BONUS, uid, 2, 0, '1', 1, 0)   # solo esito: +1
        calcola_e_aggiorna_punti_giornata(G_BONUS)
        assert _totale(uid) == 41          # calcolo di giornata: bonus incluso
    finally:
        with db_conn() as conn:
            db_execute(conn, 'DELETE FROM pronostici_iniziali WHERE id_utente = ?', (uid,))
            db_execute(conn, 'UPDATE punteggi SET bonus_finale = 0')
            db_execute(conn, 'UPDATE risultati_finali SET squadra_1 = NULL, squadra_2 = NULL, '
                             'squadra_3 = NULL, squadra_4 = NULL, capocannoniere = NULL WHERE id = 1')
            db_commit(conn)
        _pulisci_giornate(G_BONUS)


def test_ricalcolo_conta_anche_la_giornata_non_archiviata(app):
    from services.game_logic import calcola_e_aggiorna_punti_giornata, ricalcola_punteggi_totali
    uid = _crea_utente('sic_ricalcolo')
    try:
        _partita_con_pronostico(G_NON_ARCHIVIATA, uid, 1, 1, 'X', 1, 1)   # esito + esatto: 4
        calcola_e_aggiorna_punti_giornata(G_NON_ARCHIVIATA)
        dopo_calcolo = _totale(uid)
        assert dopo_calcolo >= 4
        ricalcola_punteggi_totali()
        assert _totale(uid) == dopo_calcolo   # stessa regola: il totale non cambia
    finally:
        _pulisci_giornate(G_NON_ARCHIVIATA)


# ─── Modifica giornata archiviata: piu' marcatori ────────────────────────────

G_ARCH_MOD = 95


@pytest.fixture
def giornata_archiviata():
    """Giornata archiviata con una partita 2-1 e due marcatori, la rosa delle
    due squadre e un utente che ha indovinato tutto con uno dei due marcatori."""
    uid = _crea_utente('sic_arch_utente')
    with db_conn() as conn:
        for nome, squadra in (('Lautaro Martinez', 'SIC INTER'), ('Marcus Thuram', 'SIC INTER'),
                              ('Rafael Leao', 'SIC MILAN')):
            db_execute(conn, 'INSERT INTO giocatori (nome_giocatore, squadra) VALUES (?, ?)',
                       (nome, squadra))
        db_execute(conn, 'INSERT INTO partite (giornata, squadra_casa, squadra_ospite, '
                         'risultato_casa_reale, risultato_ospite_reale, marcatore_reale, pronosticabile) '
                         'VALUES (?, ?, ?, 2, 1, ?, 1)',
                   (G_ARCH_MOD, 'SIC INTER', 'SIC MILAN', 'Lautaro Martinez, Marcus Thuram'))
        pid = row_get(db_fetchone(conn, 'SELECT id FROM partite ORDER BY id DESC LIMIT 1'), 'id')
        db_execute(conn, 'INSERT INTO stato_giornata (giornata, is_attiva, is_in_archivio) '
                         'VALUES (?, 0, 1)', (G_ARCH_MOD,))
        db_execute(conn, 'INSERT INTO pronostici_giornata (id_utente, id_partita, esito_pronosticato, '
                         'risultato_casa_pronosticato, risultato_ospite_pronosticato, '
                         'marcatore_pronosticato) VALUES (?, ?, ?, 2, 1, ?)',
                   (uid, pid, '1', 'Lautaro Martinez'))
        db_commit(conn)
    yield {'uid': uid, 'pid': pid}
    with db_conn() as conn:
        db_execute(conn, "DELETE FROM giocatori WHERE squadra IN ('SIC INTER', 'SIC MILAN')")
        db_execute(conn, 'DELETE FROM stato_giornata WHERE giornata = ?', (G_ARCH_MOD,))
        db_commit(conn)
    _pulisci_giornate(G_ARCH_MOD)


def _marcatore_salvato(pid):
    with db_conn() as conn:
        return row_get(db_fetchone(conn, 'SELECT marcatore_reale FROM partite WHERE id = ?', (pid,)),
                       'marcatore_reale')


def _punti_giornata(uid, giornata):
    with db_conn() as conn:
        return row_get(db_fetchone(conn, 'SELECT punti FROM punteggi_giornata '
                                         'WHERE id_utente = ? AND giornata = ?', (uid, giornata)), 'punti')


def test_modifica_archiviata_mostra_tutti_i_marcatori(client, giornata_archiviata):
    _crea_utente('sic_admin_arch', is_admin=True)
    _login(client, 'sic_admin_arch', admin=True)
    html = client.get(f'/admin/modifica-giornata-archiviata/{G_ARCH_MOD}').data.decode('utf-8')
    pid = giornata_archiviata['pid']
    assert html.count(f'name="marcatore_{pid}[]"') == 3        # 2 righe + modello per aggiungerne
    assert '<option value="Lautaro Martinez" selected>' in html
    assert '<option value="Marcus Thuram" selected>' in html
    assert 'value="Autogol"' in html and '+ Aggiungi marcatore' in html


def test_modifica_archiviata_salva_piu_marcatori_e_ricalcola(client, giornata_archiviata):
    """Scenario del bug: si corregge la giornata senza toccare i marcatori.
    Prima restava un solo marcatore e chi aveva indovinato l'altro perdeva punti."""
    _crea_utente('sic_admin_arch2', is_admin=True)
    _login(client, 'sic_admin_arch2', admin=True)
    pid, uid = giornata_archiviata['pid'], giornata_archiviata['uid']
    client.post(f'/admin/modifica-giornata-archiviata/{G_ARCH_MOD}', data={
        f'risultato_casa_{pid}': '2', f'risultato_ospite_{pid}': '1',
        f'marcatore_{pid}[]': ['Lautaro Martinez', 'Marcus Thuram'],
    })
    assert _marcatore_salvato(pid) == 'Lautaro Martinez, Marcus Thuram'
    # Ricalcolo automatico: esito 1 + esatto 3 + marcatore 2 + bonus 1
    assert _punti_giornata(uid, G_ARCH_MOD) == 7

    # Doppioni e righe "Nessun marcatore" vengono ignorati se ci sono giocatori
    client.post(f'/admin/modifica-giornata-archiviata/{G_ARCH_MOD}', data={
        f'risultato_casa_{pid}': '2', f'risultato_ospite_{pid}': '1',
        f'marcatore_{pid}[]': ['Marcus Thuram', 'Nessun marcatore', 'Marcus Thuram'],
    })
    assert _marcatore_salvato(pid) == 'Marcus Thuram'
    assert _punti_giornata(uid, G_ARCH_MOD) == 4                # marcatore e bonus persi, giustamente


def test_modifica_archiviata_conserva_marcatore_fuori_rosa(client, giornata_archiviata):
    pid = giornata_archiviata['pid']
    with db_conn() as conn:
        db_execute(conn, 'UPDATE partite SET marcatore_reale = ? WHERE id = ?',
                   ('Lautaro Martinez, Vecchio Nome', pid))
        db_commit(conn)
    _crea_utente('sic_admin_arch3', is_admin=True)
    _login(client, 'sic_admin_arch3', admin=True)
    html = client.get(f'/admin/modifica-giornata-archiviata/{G_ARCH_MOD}').data.decode('utf-8')
    assert 'Già salvati (non in rosa)' in html
    assert '<option value="Vecchio Nome" selected>' in html


def test_modifica_archiviata_senza_rosa_testo_libero(client):
    uid = _crea_utente('sic_admin_arch4', is_admin=True)
    with db_conn() as conn:
        db_execute(conn, 'INSERT INTO partite (giornata, squadra_casa, squadra_ospite, '
                         'risultato_casa_reale, risultato_ospite_reale, pronosticabile) '
                         'VALUES (?, ?, ?, 1, 1, 1)', (G_ARCH_MOD, 'SENZA ROSA A', 'SENZA ROSA B'))
        pid = row_get(db_fetchone(conn, 'SELECT id FROM partite ORDER BY id DESC LIMIT 1'), 'id')
        db_commit(conn)
    try:
        _login(client, 'sic_admin_arch4', admin=True)
        html = client.get(f'/admin/modifica-giornata-archiviata/{G_ARCH_MOD}').data.decode('utf-8')
        assert f'name="marcatore_{pid}"' in html and f'name="marcatore_{pid}[]"' not in html
        client.post(f'/admin/modifica-giornata-archiviata/{G_ARCH_MOD}', data={
            f'risultato_casa_{pid}': '1', f'risultato_ospite_{pid}': '1',
            f'marcatore_{pid}': 'Giocatore Uno ,  Giocatore Due',
        })
        assert _marcatore_salvato(pid) == 'Giocatore Uno, Giocatore Due'
    finally:
        _pulisci_giornate(G_ARCH_MOD)


# ─── Correzione pronostici da admin ──────────────────────────────────────────
# Riusa la giornata archiviata del fixture sopra: partita 2-1 con marcatori
# Lautaro Martinez e Marcus Thuram, utente con pronostico 1 / 2-1 / Lautaro.

def _pronostico(uid, pid):
    with db_conn() as conn:
        return db_fetchone(conn, 'SELECT * FROM pronostici_giornata WHERE id_utente = ? AND id_partita = ?',
                           (uid, pid))


def test_correzione_pronostici_form_precompilato_e_tendina(client, giornata_archiviata):
    pid, uid = giornata_archiviata['pid'], giornata_archiviata['uid']
    _crea_utente('sic_admin_corr', is_admin=True)
    _login(client, 'sic_admin_corr', admin=True)
    html = client.get(f'/admin/gestisci-pronostici/{G_ARCH_MOD}').data.decode('utf-8')
    # Tendina, non piu' campo di testo libero
    assert f'<select name="marcatore" id="marcatore-{pid}"' in html
    assert 'list="marcatori-' not in html
    assert '<option value="Lautaro Martinez">' in html and '<option value="Autogol">' in html
    # Dati per precompilare il form: il pronostico esistente dell'utente
    dati = re.search(rf'<script type="application/json" id="pronostici-{pid}">(.*?)</script>', html, re.S)
    salvato = json.loads(dati.group(1))[str(uid)]
    assert salvato == {'esito': '1', 'casa': 2, 'ospite': 1, 'marcatore': 'Lautaro Martinez'}
    assert 'sic_arch_utente ✓' in html                          # chi ha gia' pronosticato
    assert f'modificaPronostico({pid}, {uid})' in html           # bottone Modifica sulla riga


def test_correzione_marcatore_scritto_male_rifiutata(client, giornata_archiviata):
    pid, uid = giornata_archiviata['pid'], giornata_archiviata['uid']
    _crea_utente('sic_admin_corr2', is_admin=True)
    _login(client, 'sic_admin_corr2', admin=True)
    html = client.post(f'/admin/gestisci-pronostici/{G_ARCH_MOD}', data={
        'action': 'salva', 'id_partita': str(pid), 'id_utente': str(uid),
        'esito': '1', 'risultato_casa': '2', 'risultato_ospite': '1',
        'marcatore': 'Lautaro Martines',
    }, follow_redirects=True).data.decode('utf-8')
    assert 'non valido' in html
    assert row_get(_pronostico(uid, pid), 'marcatore_pronosticato') == 'Lautaro Martinez'


def test_correzione_ricalcola_i_punti_da_sola(client, giornata_archiviata):
    """Si corregge solo il marcatore (il form precompilato rimanda gli altri
    campi): esito e risultato restano, i punti si aggiornano senza ricalcolo."""
    pid, uid = giornata_archiviata['pid'], giornata_archiviata['uid']
    _crea_utente('sic_admin_corr3', is_admin=True)
    _login(client, 'sic_admin_corr3', admin=True)
    html = client.post(f'/admin/gestisci-pronostici/{G_ARCH_MOD}', data={
        'action': 'salva', 'id_partita': str(pid), 'id_utente': str(uid),
        'esito': '1', 'risultato_casa': '2', 'risultato_ospite': '1',
        'marcatore': 'Rafael Leao',
    }, follow_redirects=True).data.decode('utf-8')
    assert 'classifica aggiornati' in html
    p = _pronostico(uid, pid)
    assert (row_get(p, 'esito_pronosticato'), row_get(p, 'risultato_casa_pronosticato'),
            row_get(p, 'risultato_ospite_pronosticato'), row_get(p, 'marcatore_pronosticato')) \
        == ('1', 2, 1, 'Rafael Leao')
    assert _punti_giornata(uid, G_ARCH_MOD) == 4                 # esito + esatto, marcatore sbagliato

    id_pron = row_get(p, 'id')
    client.post(f'/admin/gestisci-pronostici/{G_ARCH_MOD}',
                data={'action': 'cancella', 'id_pronostico': str(id_pron)})
    assert _punti_giornata(uid, G_ARCH_MOD) == 0                 # anche l'eliminazione ricalcola


def test_correzione_partita_di_altra_giornata_rifiutata(client, giornata_archiviata):
    pid, uid = giornata_archiviata['pid'], giornata_archiviata['uid']
    _crea_utente('sic_admin_corr4', is_admin=True)
    _login(client, 'sic_admin_corr4', admin=True)
    html = client.post('/admin/gestisci-pronostici/1', data={
        'action': 'salva', 'id_partita': str(pid), 'id_utente': str(uid),
        'esito': '2', 'risultato_casa': '0', 'risultato_ospite': '1', 'marcatore': '',
    }, follow_redirects=True).data.decode('utf-8')
    assert 'Partita non trovata' in html
    assert row_get(_pronostico(uid, pid), 'esito_pronosticato') == '1'


# ─── Archivio: "Modifica risultati" a ogni admin, non solo a mirko ───────────

def test_archivio_modifica_risultati_per_ogni_admin(client, giornata_archiviata):
    link = f'/admin/modifica-giornata-archiviata/{G_ARCH_MOD}'
    _crea_utente('sic_nuovo_admin', is_admin=True)
    _login(client, 'sic_nuovo_admin', admin=True)
    assert link in client.get('/giornate').data.decode('utf-8')
    _crea_utente('sic_non_admin')
    _login(client, 'sic_non_admin')
    assert link not in client.get('/giornate').data.decode('utf-8')


def test_nessun_controllo_admin_sul_nome_utente():
    cartella = os.path.join(BASE_DIR, 'templates')
    for nome in os.listdir(cartella):
        testo = open(os.path.join(cartella, nome), encoding='utf-8').read()
        assert "== 'mirko'" not in testo, nome


# ─── Pulsanti "torna indietro" uguali ovunque e luce dei link admin ─────────

def test_pulsanti_indietro_uguali_in_tutte_le_pagine():
    cartella = os.path.join(BASE_DIR, 'templates')
    for nome in os.listdir(cartella):
        testo = open(os.path.join(cartella, nome), encoding='utf-8').read()
        assert 'back-link' not in testo, nome            # vecchio stile in cima alle pagine
        assert '← ' not in testo, nome                   # freccia scritta come testo (admin)
        for a in re.findall(r'<a [^>]*class="btn-back"[^>]*>.*?</a>', testo, re.S):
            assert 'd="M15 6l-6 6 6 6"' in a, (nome, a)  # stessa icona ovunque
    # Nelle pagine con due pulsanti "indietro", in cima e in fondo sono identici
    for nome in ('archivio_giornate.html', 'admin_utenti.html', 'admin_gestisci_partite.html',
                 'visualizza_giornata.html', 'classifica_cumulativa.html'):
        testo = open(os.path.join(cartella, nome), encoding='utf-8').read()
        bottoni = re.findall(r'<a [^>]*class="btn-back"[^>]*>.*?</a>', testo, re.S)
        assert len(bottoni) == 2 and bottoni[0] == bottoni[1], nome


def test_css_luce_pulsante_indietro_e_link_admin():
    css = open(os.path.join(BASE_DIR, 'static', 'css', 'app.css'), encoding='utf-8').read()
    for cls in ('btn-back', 'u-glow'):
        assert re.search(rf'\.{cls}:hover\s*{{[^}}]*box-shadow', css), cls
        assert re.search(rf'\.{cls}:active\s*{{[^}}]*box-shadow', css), cls
    admin = open(os.path.join(BASE_DIR, 'templates', 'admin.html'), encoding='utf-8').read()
    assert re.search(r'admin_gestisci_partite\'\) }}" class="u-glow"', admin)


# ─── Email di nuova giornata ─────────────────────────────────────────────────

def test_form_import_ha_la_casella_email():
    testo = open(os.path.join(BASE_DIR, 'templates', 'admin_importa_giornata.html'),
                 encoding='utf-8').read()
    assert 'name="invia_email"' in testo
    # non spuntata di default: e' un promemoria di riserva
    assert 'checked' not in testo.split('name="invia_email"')[1].split('>')[0]


# ─── Installazione PWA ───────────────────────────────────────────────────────

def test_manifest_e_icone_raggiungibili(client):
    html = client.get('/login').data.decode('utf-8')
    assert 'rel="manifest" href="/static/manifest.json"' in html
    assert 'apple-touch-icon' in html
    r = client.get('/static/manifest.json')
    assert r.status_code == 200
    manifest = json.loads(r.data)
    r.close()
    assert manifest['display'] == 'standalone' and manifest['start_url'] == '/'
    misure = {i['sizes'] for i in manifest['icons']}
    assert {'192x192', '512x512'} <= misure
    assert any(i.get('purpose') == 'maskable' for i in manifest['icons'])
    for icona in manifest['icons']:
        r = client.get(icona['src'])
        assert r.status_code == 200, icona['src']
        r.close()
    sw = client.get('/sw.js').data.decode('utf-8')
    for percorso in ('/static/icons/icon-192.png', '/static/icons/badge-96.png'):
        assert percorso in sw
        r = client.get(percorso)
        assert r.status_code == 200, percorso
        r.close()


# ─── Utente rinominato mentre è collegato ────────────────────────────────────

def test_utente_rinominato_torna_al_login(client):
    uid = _crea_utente('sic_da_rinominare')
    _login(client, 'sic_da_rinominare')
    assert client.get('/classifica').status_code == 200
    with db_conn() as conn:
        db_execute(conn, "UPDATE utenti SET nome_utente = 'sic_rinominato' WHERE id = ?", (uid,))
        db_commit(conn)
    r = client.get('/classifica')
    assert r.status_code == 302 and '/login' in r.headers['Location']
    with client.session_transaction() as s:
        assert 'nome_utente' not in s


def test_admin_che_rinomina_se_stesso_resta_collegato(client):
    uid = _crea_utente('sic_admin_self', is_admin=True)
    _login(client, 'sic_admin_self', admin=True)
    client.post(f'/admin/rinomina-utente/{uid}', data={'nuovo_nome_utente': 'sic_admin_nuovo'})
    with client.session_transaction() as s:
        assert s['nome_utente'] == 'sic_admin_nuovo'
    assert client.get('/classifica').status_code == 200
