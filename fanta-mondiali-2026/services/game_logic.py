"""
Logica di gioco Fanta Mondiali 2026 — versione semplificata.

Un solo sistema punti per TUTTI i match (gironi + eliminazione diretta):
  - Esito corretto (1/X/2)  → +1 pt
  - Risultato esatto         → +3 pt
  - Marcatore corretto       → +2 pt
  - Bonus tripla             → +1 pt

Pronostici torneo (pre-partenza):
  - Vincitore del torneo     → +40 pt
  - Capocannoniere           → +25 pt
"""

import logging
import re
from datetime import datetime

import pytz

from db_utils import (
    db_conn, db_execute, db_fetchone, db_fetchall,
    db_commit, row_get, USE_POSTGRES,
)

log = logging.getLogger('mondiali')

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

PUNTI_ESITO        = 1
PUNTI_RISULTATO    = 3
PUNTI_MARCATORE    = 2
PUNTI_BONUS_TRIPLA = 1

PUNTI_TORNEO = {
    'vincitore':      40,
    'capocannoniere': 25,
}

FASI_ORDINE = ['gironi', 'r32', 'r16', 'qf', 'sf', 'finale']
FASI_NOMI = {
    'gironi': 'Fase a Gironi',
    'r32':    'Round of 32',
    'r16':    'Ottavi di Finale',
    'qf':     'Quarti di Finale',
    'sf':     'Semifinali',
    'finale': 'Finale',
    '3posto': '3° posto',
}


def parse_flexible_datetime(date_string: str):
    if not date_string:
        return None
    for fmt in ('%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%dT%H:%M:%S',
                '%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            return datetime.strptime(date_string, fmt)
        except ValueError:
            continue
    return None


def is_partita_scaduta(data_ora_utc_str) -> bool:
    if not data_ora_utc_str:
        return False
    naive = parse_flexible_datetime(str(data_ora_utc_str))
    if not naive:
        return False
    roma_tz = pytz.timezone('Europe/Rome')
    return datetime.now(roma_tz) > pytz.utc.localize(naive).astimezone(roma_tz)


def utc_to_rome(data_ora_utc_str) -> str:
    if not data_ora_utc_str:
        return ''
    try:
        naive = parse_flexible_datetime(str(data_ora_utc_str))
        if not naive:
            return str(data_ora_utc_str)
        roma_tz = pytz.timezone('Europe/Rome')
        return pytz.utc.localize(naive).astimezone(roma_tz).strftime('%d/%m %H:%M')
    except Exception:
        return str(data_ora_utc_str)


def calcola_punti_pronostico(pronostico, partita) -> dict:
    out = {
        'esito': 0, 'risultato': 0, 'marcatore': 0, 'bonus': 0, 'totale': 0,
        'esito_corretto': False, 'risultato_corretto': False,
        'marcatore_corretto': False,
    }
    if not pronostico:
        return out
    r_casa = row_get(partita, 'risultato_casa_reale')
    r_osp  = row_get(partita, 'risultato_ospite_reale')
    if r_casa is None or r_osp is None:
        return out
    esito_reale = '1' if r_casa > r_osp else 'X' if r_casa == r_osp else '2'
    if row_get(pronostico, 'esito_pronosticato') == esito_reale:
        out['esito'] = PUNTI_ESITO
        out['esito_corretto'] = True
    if (row_get(pronostico, 'risultato_casa_pronosticato') == r_casa and
            row_get(pronostico, 'risultato_ospite_pronosticato') == r_osp):
        out['risultato'] = PUNTI_RISULTATO
        out['risultato_corretto'] = True
    pm = (row_get(pronostico, 'marcatore_pronosticato') or '').strip().lower()
    mr_raw = row_get(partita, 'marcatore_reale') or ''
    marcatori_reali = [m.strip().lower() for m in mr_raw.split(',') if m.strip()]
    if pm == 'nessun marcatore':
        if r_casa == 0 and r_osp == 0:
            out['marcatore'] = PUNTI_MARCATORE
            out['marcatore_corretto'] = True
    elif pm and pm in marcatori_reali:
        out['marcatore'] = PUNTI_MARCATORE
        out['marcatore_corretto'] = True
    if out['esito_corretto'] and out['risultato_corretto'] and out['marcatore_corretto']:
        out['bonus'] = PUNTI_BONUS_TRIPLA
    out['totale'] = out['esito'] + out['risultato'] + out['marcatore'] + out['bonus']
    return out


def _safe_int(v, lo=None, hi=None):
    if v is None or v == '':
        return None
    try:
        n = int(v)
    except (ValueError, TypeError):
        return None
    if lo is not None and n < lo:
        return None
    if hi is not None and n > hi:
        return None
    return n


def _upsert_punteggio_giornata(conn, id_utente, giornata, punti):
    q = ('INSERT INTO punteggi_giornata (id_utente, giornata, punti) VALUES (?,?,?) '
         'ON CONFLICT (id_utente, giornata) DO UPDATE SET punti = {}punti'.format(
             'EXCLUDED.' if USE_POSTGRES else 'excluded.'))
    db_execute(conn, q, (id_utente, giornata, punti))


def _upsert_punteggio_fase(conn, id_utente, fase, punti):
    q = ('INSERT INTO punteggi_fase (id_utente, fase, punti) VALUES (?,?,?) '
         'ON CONFLICT (id_utente, fase) DO UPDATE SET punti = {}punti'.format(
             'EXCLUDED.' if USE_POSTGRES else 'excluded.'))
    db_execute(conn, q, (id_utente, fase, punti))


def _calcola_punti_giornata_conn(giornata, conn):
    utenti  = db_fetchall(conn, 'SELECT id FROM utenti')
    partite = db_fetchall(conn,
        'SELECT * FROM partite WHERE giornata=? AND pronosticabile=TRUE '
        'AND risultato_casa_reale IS NOT NULL', (giornata,))
    if not partite:
        return
    pids = [row_get(p, 'id') for p in partite]
    ph   = ','.join(['?'] * len(pids))
    rows = db_fetchall(conn,
        f'SELECT * FROM pronostici_giornata WHERE id_partita IN ({ph})',
        tuple(pids))
    pron_idx  = {(row_get(r, 'id_utente'), row_get(r, 'id_partita')): r for r in rows}
    parte_idx = {row_get(p, 'id'): p for p in partite}
    for u in utenti:
        uid = row_get(u, 'id')
        punti = sum(
            calcola_punti_pronostico(pron_idx.get((uid, pid)), parte_idx[pid])['totale']
            for pid in pids)
        _upsert_punteggio_giornata(conn, uid, giornata, punti)


def calcola_e_aggiorna_punti_giornata(giornata) -> str:
    with db_conn() as conn:
        if not db_fetchall(conn,
            'SELECT id FROM partite WHERE giornata=? AND pronosticabile=TRUE '
            'AND risultato_casa_reale IS NOT NULL', (giornata,)):
            return f'Nessuna partita con risultati per la giornata {giornata}.'
        _calcola_punti_giornata_conn(giornata, conn)
        _refresh_totali_tutti(conn)
        db_commit(conn)
    return f'Punteggi giornata {giornata} calcolati!'


def calcola_e_aggiorna_punti_fase(fase) -> str:
    with db_conn() as conn:
        partite = db_fetchall(conn,
            'SELECT * FROM partite WHERE fase=? AND risultato_casa_reale IS NOT NULL',
            (fase,))
        if not partite:
            return f'Nessuna partita con risultati per {FASI_NOMI.get(fase, fase)}.'
        utenti = db_fetchall(conn, 'SELECT id FROM utenti')
        pids   = [row_get(p, 'id') for p in partite]
        ph     = ','.join(['?'] * len(pids))
        prons  = db_fetchall(conn,
            f'SELECT * FROM pronostici_eliminazione WHERE id_partita IN ({ph})',
            tuple(pids))
        pron_idx  = {(row_get(r, 'id_utente'), row_get(r, 'id_partita')): r for r in prons}
        parte_idx = {row_get(p, 'id'): p for p in partite}
        for u in utenti:
            uid = row_get(u, 'id')
            punti = sum(
                calcola_punti_pronostico(pron_idx.get((uid, pid)), parte_idx[pid])['totale']
                for pid in pids)
            _upsert_punteggio_fase(conn, uid, fase, punti)
        _refresh_totali_tutti(conn)
        db_commit(conn)
    return f'Punteggi {FASI_NOMI.get(fase, fase)} calcolati!'


def calcola_punti_torneo(pronostico, risultati) -> dict:
    out = {'vincitore': 0, 'capocannoniere': 0, 'totale': 0,
           'vincitore_ok': False, 'capocannoniere_ok': False}
    if not pronostico or not risultati:
        return out
    def match(cp, cr):
        return ((row_get(pronostico, cp) or '').strip().lower() ==
                (row_get(risultati, cr)  or '').strip().lower())
    if match('vincitore', 'vincitore'):
        out['vincitore']    = PUNTI_TORNEO['vincitore']
        out['vincitore_ok'] = True
    if match('capocannoniere', 'capocannoniere'):
        out['capocannoniere']    = PUNTI_TORNEO['capocannoniere']
        out['capocannoniere_ok'] = True
    out['totale'] = out['vincitore'] + out['capocannoniere']
    return out


def calcola_e_aggiorna_punti_torneo() -> str:
    with db_conn() as conn:
        rf = db_fetchone(conn, 'SELECT * FROM risultati_torneo WHERE id=1')
        if not rf or not row_get(rf, 'vincitore'):
            return 'Inserisci prima vincitore e capocannoniere reali.'
        for u in db_fetchall(conn, 'SELECT id FROM utenti'):
            uid  = row_get(u, 'id')
            pron = db_fetchone(conn,
                'SELECT * FROM pronostici_torneo WHERE id_utente=?', (uid,))
            det = calcola_punti_torneo(pron, rf)
            _upsert_punteggio_fase(conn, uid, 'torneo', det['totale'])
        _refresh_totali_tutti(conn)
        db_commit(conn)
    return 'Bonus torneo calcolati!'


def _refresh_totali_tutti(conn):
    for u in db_fetchall(conn, 'SELECT id FROM utenti'):
        uid = row_get(u, 'id')
        r1  = db_fetchone(conn,
            'SELECT COALESCE(SUM(punti),0) AS tot FROM punteggi_giornata WHERE id_utente=?',
            (uid,))
        r2  = db_fetchone(conn,
            'SELECT COALESCE(SUM(punti),0) AS tot FROM punteggi_fase WHERE id_utente=?',
            (uid,))
        totale = (row_get(r1, 'tot') or 0) + (row_get(r2, 'tot') or 0)
        q = ('INSERT INTO punteggi (id_utente, punteggio_totale) VALUES (?,?) '
             'ON CONFLICT (id_utente) DO UPDATE SET punteggio_totale = {}punteggio_totale'.format(
                 'EXCLUDED.' if USE_POSTGRES else 'excluded.'))
        db_execute(conn, q, (uid, totale))


def ricalcola_tutto() -> str:
    with db_conn() as conn:
        db_execute(conn, 'DELETE FROM punteggi_giornata')
        db_execute(conn, 'DELETE FROM punteggi_fase')
        db_execute(conn, 'DELETE FROM punteggi')
        for u in db_fetchall(conn, 'SELECT id FROM utenti'):
            db_execute(conn,
                'INSERT INTO punteggi (id_utente, punteggio_totale) VALUES (?,0)',
                (row_get(u, 'id'),))
        for g in db_fetchall(conn,
            'SELECT giornata FROM stato_giornata WHERE is_in_archivio=TRUE'):
            _calcola_punti_giornata_conn(row_get(g, 'giornata'), conn)
        for fase in ['r32', 'r16', 'qf', 'sf', 'finale', '3posto']:
            partite = db_fetchall(conn,
                'SELECT * FROM partite WHERE fase=? AND risultato_casa_reale IS NOT NULL',
                (fase,))
            if not partite:
                continue
            pids      = [row_get(p, 'id') for p in partite]
            ph        = ','.join(['?'] * len(pids))
            prons     = db_fetchall(conn,
                f'SELECT * FROM pronostici_eliminazione WHERE id_partita IN ({ph})',
                tuple(pids))
            pron_idx  = {(row_get(r, 'id_utente'), row_get(r, 'id_partita')): r for r in prons}
            parte_idx = {row_get(p, 'id'): p for p in partite}
            for u in db_fetchall(conn, 'SELECT id FROM utenti'):
                uid = row_get(u, 'id')
                punti = sum(
                    calcola_punti_pronostico(pron_idx.get((uid, pid)), parte_idx[pid])['totale']
                    for pid in pids)
                _upsert_punteggio_fase(conn, uid, fase, punti)
        rf = db_fetchone(conn, 'SELECT * FROM risultati_torneo WHERE id=1')
        if rf and row_get(rf, 'vincitore'):
            for u in db_fetchall(conn, 'SELECT id FROM utenti'):
                uid  = row_get(u, 'id')
                pron = db_fetchone(conn,
                    'SELECT * FROM pronostici_torneo WHERE id_utente=?', (uid,))
                det = calcola_punti_torneo(pron, rf)
                _upsert_punteggio_fase(conn, uid, 'torneo', det['totale'])
        _refresh_totali_tutti(conn)
        db_commit(conn)
    return 'Classifica ricalcolata!'


def invia_reminder_automatici(app):
    """Controlla partite nelle prossime 1-3 ore e invia reminder. Chiamata ogni 30 min."""
    try:
        from services.email_service import invia_email_async, build_email_giornata
        roma_tz = pytz.timezone('Europe/Rome')
        ora_it  = datetime.now(roma_tz)
        with app.app_context():
            with db_conn() as conn:
                partite = db_fetchall(conn,
                    'SELECT * FROM partite WHERE pronosticabile=TRUE '
                    'AND reminder_inviato=FALSE AND data_ora_partita IS NOT NULL')
            da_inviare = []
            for p in partite:
                naive = parse_flexible_datetime(str(row_get(p, 'data_ora_partita')))
                if not naive:
                    continue
                ora_it_p = pytz.utc.localize(naive).astimezone(roma_tz)
                diff_h = (ora_it_p - ora_it).total_seconds() / 3600
                if 1.0 <= diff_h <= 3.0:
                    da_inviare.append(p)
            if not da_inviare:
                return
            with db_conn() as conn:
                utenti_email = db_fetchall(conn,
                    "SELECT email FROM utenti WHERE email IS NOT NULL AND email != ''")
            destinatari = [row_get(u, 'email') for u in utenti_email if row_get(u, 'email')]
            if not destinatari:
                return
            for g in set(row_get(p, 'giornata') for p in da_inviare if row_get(p, 'giornata')):
                pl = [{'squadra_casa': row_get(p, 'squadra_casa'),
                       'squadra_ospite': row_get(p, 'squadra_ospite'),
                       'data_ora_partita': row_get(p, 'data_ora_partita')}
                      for p in da_inviare if row_get(p, 'giornata') == g]
                invia_email_async(
                    destinatari,
                    f'⚽ Fanta Mondiali — Round {g}: inserisci i pronostici!',
                    build_email_giornata(g, pl))
            for fase in set(row_get(p, 'fase') for p in da_inviare
                            if row_get(p, 'fase') and row_get(p, 'fase') != 'gironi'):
                pl = [{'squadra_casa': row_get(p, 'squadra_casa'),
                       'squadra_ospite': row_get(p, 'squadra_ospite'),
                       'data_ora_partita': row_get(p, 'data_ora_partita')}
                      for p in da_inviare if row_get(p, 'fase') == fase]
                invia_email_async(
                    destinatari,
                    f'🏆 Fanta Mondiali — {FASI_NOMI.get(fase, fase)}: inserisci i pronostici!',
                    build_email_giornata(fase, pl))
            pids = [row_get(p, 'id') for p in da_inviare]
            ph   = ','.join(['?'] * len(pids))
            with db_conn() as conn:
                db_execute(conn,
                    f'UPDATE partite SET reminder_inviato=TRUE WHERE id IN ({ph})',
                    tuple(pids))
                db_commit(conn)
            log.info(f'[REMINDER] Inviati per {len(da_inviare)} partite a {len(destinatari)} utenti.')
    except Exception:
        log.exception('[REMINDER] Errore')
