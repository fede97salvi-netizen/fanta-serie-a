"""
Logica di gioco per Fanta Mondiali 2026.

Tre strati di punteggio:
  A) Fase a gironi     — identica a FantaSerieA (esito/risultato/marcatore)
  B) Eliminazione diretta — chi vince + bonus risultato nei 90'
  C) Pronostici torneo — vincitore/finalista/semi/capocannoniere (pre-torneo)
"""

import logging
import re
from datetime import datetime

import pytz

from db_utils import (
    db_conn, db_execute, db_fetchone, db_fetchall, db_commit, row_get, USE_POSTGRES
)

log = logging.getLogger('mondiali')

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

# ─── Costanti ─────────────────────────────────────────────────────────────────

# Fase a gironi (stessa formula FantaSerieA)
PUNTI_ESITO        = 1
PUNTI_RISULTATO    = 3
PUNTI_MARCATORE    = 2
PUNTI_BONUS_TRIPLA = 1

# Fase eliminazione diretta — punti crescenti per importanza del turno
PUNTI_KNOCKOUT = {
    'r32':    {'vincitore': 3,  'risultato_bonus': 2},
    'r16':    {'vincitore': 5,  'risultato_bonus': 2},
    'qf':     {'vincitore': 8,  'risultato_bonus': 2},
    'sf':     {'vincitore': 12, 'risultato_bonus': 3},
    'finale': {'vincitore': 15, 'risultato_bonus': 5},
    '3posto': {'vincitore': 8,  'risultato_bonus': 2},
}

# Pronostici torneo (pre-torneo)
PUNTI_TORNEO = {
    'vincitore':              40,
    'finalista':              20,
    'semifinalista':          10,  # per ciascuno dei 2
    'capocannoniere':         25,
    'bonus_vincitore_finalista': 10,
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


# ─── Utility data / timezone ─────────────────────────────────────────────────

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
    orario_naive = parse_flexible_datetime(str(data_ora_utc_str))
    if not orario_naive:
        return False
    roma_tz = pytz.timezone('Europe/Rome')
    return datetime.now(roma_tz) > pytz.utc.localize(orario_naive).astimezone(roma_tz)


# ─── STRATO A: Fase a gironi ─────────────────────────────────────────────────

def calcola_punti_pronostico(pronostico, partita) -> dict:
    """Calcola punti per un pronostico di giornata (fase gironi). Identico a FantaSerieA."""
    out = {
        'esito': 0, 'risultato': 0, 'marcatore': 0, 'bonus': 0, 'totale': 0,
        'esito_corretto': False, 'risultato_corretto': False, 'marcatore_corretto': False,
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

    if (row_get(pronostico, 'risultato_casa_pronosticato') == r_casa
            and row_get(pronostico, 'risultato_ospite_pronosticato') == r_osp):
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
    if USE_POSTGRES:
        db_execute(conn,
                   'INSERT INTO punteggi_giornata (id_utente, giornata, punti) VALUES (?,?,?) '
                   'ON CONFLICT (id_utente, giornata) DO UPDATE SET punti = EXCLUDED.punti',
                   (id_utente, giornata, punti))
    else:
        db_execute(conn,
                   'INSERT INTO punteggi_giornata (id_utente, giornata, punti) VALUES (?,?,?) '
                   'ON CONFLICT (id_utente, giornata) DO UPDATE SET punti = excluded.punti',
                   (id_utente, giornata, punti))


def _calcola_punti_giornata_conn(giornata, conn):
    """Calcola e persiste i punti di una giornata gironi. Idempotente."""
    utenti  = db_fetchall(conn, 'SELECT id FROM utenti')
    partite = db_fetchall(conn,
                          'SELECT * FROM partite WHERE giornata=? AND pronosticabile=TRUE '
                          'AND risultato_casa_reale IS NOT NULL', (giornata,))
    if not partite:
        return
    pids = [row_get(p, 'id') for p in partite]
    ph = ','.join(['?'] * len(pids))
    rows = db_fetchall(conn,
                       f'SELECT * FROM pronostici_giornata WHERE id_partita IN ({ph})',
                       tuple(pids))
    pron_idx  = {(row_get(r, 'id_utente'), row_get(r, 'id_partita')): r for r in rows}
    parte_idx = {row_get(p, 'id'): p for p in partite}

    for u in utenti:
        uid = row_get(u, 'id')
        punti = sum(
            calcola_punti_pronostico(pron_idx.get((uid, pid)), parte_idx[pid])['totale']
            for pid in pids
        )
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


# ─── STRATO B: Eliminazione diretta ──────────────────────────────────────────

def calcola_punti_eliminazione(pronostico, partita) -> dict:
    """
    Calcola punti per un pronostico di eliminazione diretta.
    
    pronostico: ha campi vincitore, gol_casa_90, gol_ospite_90
    partita:    ha campi vincitore (chi ha vinto), gol_casa_90, gol_ospite_90
                (risultato nei 90' — non include extra time/rigori)
    """
    fase = row_get(partita, 'fase') or 'r32'
    cfg  = PUNTI_KNOCKOUT.get(fase, PUNTI_KNOCKOUT['r32'])

    out = {'vincitore': 0, 'risultato_bonus': 0, 'totale': 0,
           'vincitore_corretto': False, 'risultato_corretto': False}

    if not pronostico:
        return out

    vincitore_reale = (row_get(partita, 'vincitore') or '').strip()
    if not vincitore_reale:
        return out  # match non ancora giocato

    vincitore_pron = (row_get(pronostico, 'vincitore') or '').strip()
    if vincitore_pron.lower() == vincitore_reale.lower():
        out['vincitore'] = cfg['vincitore']
        out['vincitore_corretto'] = True

        # Bonus risultato nei 90'
        gc90 = row_get(partita, 'gol_casa_90')
        go90 = row_get(partita, 'gol_ospite_90')
        if gc90 is not None and go90 is not None:
            gc90_p = row_get(pronostico, 'gol_casa_90')
            go90_p = row_get(pronostico, 'gol_ospite_90')
            if gc90_p == gc90 and go90_p == go90:
                out['risultato_bonus'] = cfg['risultato_bonus']
                out['risultato_corretto'] = True

    out['totale'] = out['vincitore'] + out['risultato_bonus']
    return out


def _upsert_punteggio_fase(conn, id_utente, fase, punti):
    if USE_POSTGRES:
        db_execute(conn,
                   'INSERT INTO punteggi_fase (id_utente, fase, punti) VALUES (?,?,?) '
                   'ON CONFLICT (id_utente, fase) DO UPDATE SET punti = EXCLUDED.punti',
                   (id_utente, fase, punti))
    else:
        db_execute(conn,
                   'INSERT INTO punteggi_fase (id_utente, fase, punti) VALUES (?,?,?) '
                   'ON CONFLICT (id_utente, fase) DO UPDATE SET punti = excluded.punti',
                   (id_utente, fase, punti))


def calcola_e_aggiorna_punti_fase(fase) -> str:
    """Calcola e persiste i punti di una fase knockout. Idempotente."""
    with db_conn() as conn:
        partite = db_fetchall(conn,
                              'SELECT * FROM partite WHERE fase=? AND vincitore IS NOT NULL',
                              (fase,))
        if not partite:
            return f'Nessuna partita con risultati per la fase {FASI_NOMI.get(fase, fase)}.'

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
                calcola_punti_eliminazione(pron_idx.get((uid, pid)), parte_idx[pid])['totale']
                for pid in pids
            )
            _upsert_punteggio_fase(conn, uid, fase, punti)

        _refresh_totali_tutti(conn)
        db_commit(conn)
    return f'Punteggi {FASI_NOMI.get(fase, fase)} calcolati!'


# ─── STRATO C: Pronostici torneo ─────────────────────────────────────────────

def calcola_punti_torneo(pronostico, risultati) -> dict:
    """
    Calcola i bonus da pronostici torneo (inseriti prima del via).
    
    pronostico: dict con vincitore/finalista/semifinalista_1/semifinalista_2/capocannoniere
    risultati:  dict con gli stessi campi compilati dall'admin a fine torneo
    """
    out = {
        'vincitore': 0, 'finalista': 0, 'semifinalista': 0,
        'capocannoniere': 0, 'bonus': 0, 'totale': 0,
        'vincitore_ok': False, 'finalista_ok': False,
        'semi1_ok': False, 'semi2_ok': False, 'capocannoniere_ok': False,
    }
    if not pronostico or not risultati:
        return out

    def match(campo_pron, campo_ris):
        return (row_get(pronostico, campo_pron) or '').strip().lower() == \
               (row_get(risultati, campo_ris)  or '').strip().lower()

    if match('vincitore', 'vincitore'):
        out['vincitore']    = PUNTI_TORNEO['vincitore']
        out['vincitore_ok'] = True

    if match('finalista', 'finalista'):
        out['finalista']    = PUNTI_TORNEO['finalista']
        out['finalista_ok'] = True

    if out['vincitore_ok'] and out['finalista_ok']:
        out['bonus'] = PUNTI_TORNEO['bonus_vincitore_finalista']

    semi_reali = {
        (row_get(risultati, 'semifinalista_1') or '').strip().lower(),
        (row_get(risultati, 'semifinalista_2') or '').strip().lower(),
    }
    for campo, flag in [('semifinalista_1', 'semi1_ok'), ('semifinalista_2', 'semi2_ok')]:
        v = (row_get(pronostico, campo) or '').strip().lower()
        if v and v in semi_reali:
            out['semifinalista'] += PUNTI_TORNEO['semifinalista']
            out[flag] = True

    if match('capocannoniere', 'capocannoniere'):
        out['capocannoniere']    = PUNTI_TORNEO['capocannoniere']
        out['capocannoniere_ok'] = True

    out['totale'] = (out['vincitore'] + out['finalista'] + out['semifinalista']
                     + out['capocannoniere'] + out['bonus'])
    return out


def calcola_e_aggiorna_punti_torneo() -> str:
    """Calcola i bonus torneo (chiamato a fine torneo). Idempotente."""
    with db_conn() as conn:
        rf = db_fetchone(conn, 'SELECT * FROM risultati_torneo WHERE id=1')
        if not rf or not row_get(rf, 'vincitore'):
            return 'Inserisci prima i risultati reali del torneo (vincitore, finalista ecc.).'

        utenti = db_fetchall(conn, 'SELECT id FROM utenti')
        for u in utenti:
            uid  = row_get(u, 'id')
            pron = db_fetchone(conn,
                               'SELECT * FROM pronostici_torneo WHERE id_utente=?', (uid,))
            det  = calcola_punti_torneo(pron, rf)
            if USE_POSTGRES:
                db_execute(conn,
                           'INSERT INTO punteggi_fase (id_utente, fase, punti) VALUES (?,?,?) '
                           'ON CONFLICT (id_utente, fase) DO UPDATE SET punti = EXCLUDED.punti',
                           (uid, 'torneo', det['totale']))
            else:
                db_execute(conn,
                           'INSERT INTO punteggi_fase (id_utente, fase, punti) VALUES (?,?,?) '
                           'ON CONFLICT (id_utente, fase) DO UPDATE SET punti = excluded.punti',
                           (uid, 'torneo', det['totale']))

        _refresh_totali_tutti(conn)
        db_commit(conn)
    return 'Bonus pronostici torneo calcolati!'


# ─── Ricalcolo totali ────────────────────────────────────────────────────────

def _refresh_totali_tutti(conn):
    """Aggiorna punteggio_totale per tutti gli utenti dalla somma di punteggi_giornata + punteggi_fase."""
    utenti = db_fetchall(conn, 'SELECT id FROM utenti')
    for u in utenti:
        uid = row_get(u, 'id')
        r1 = db_fetchone(conn,
                         'SELECT COALESCE(SUM(punti),0) AS tot FROM punteggi_giornata WHERE id_utente=?',
                         (uid,))
        r2 = db_fetchone(conn,
                         'SELECT COALESCE(SUM(punti),0) AS tot FROM punteggi_fase WHERE id_utente=?',
                         (uid,))
        totale = (row_get(r1, 'tot') or 0) + (row_get(r2, 'tot') or 0)
        if USE_POSTGRES:
            db_execute(conn,
                       'INSERT INTO punteggi (id_utente, punteggio_totale) VALUES (?,?) '
                       'ON CONFLICT (id_utente) DO UPDATE SET punteggio_totale = EXCLUDED.punteggio_totale',
                       (uid, totale))
        else:
            db_execute(conn,
                       'INSERT INTO punteggi (id_utente, punteggio_totale) VALUES (?,?) '
                       'ON CONFLICT (id_utente) DO UPDATE SET punteggio_totale = excluded.punteggio_totale',
                       (uid, totale))


def ricalcola_tutto() -> str:
    """Ricalcola tutti i punteggi da zero (idempotente)."""
    with db_conn() as conn:
        db_execute(conn, 'DELETE FROM punteggi_giornata')
        db_execute(conn, 'DELETE FROM punteggi_fase')
        db_execute(conn, 'DELETE FROM punteggi')
        for u in db_fetchall(conn, 'SELECT id FROM utenti'):
            db_execute(conn,
                       'INSERT INTO punteggi (id_utente, punteggio_totale) VALUES (?,0)',
                       (row_get(u, 'id'),))

        # Ricalcola gironi
        for g in db_fetchall(conn,
                             'SELECT giornata FROM stato_giornata WHERE is_in_archivio=TRUE'):
            _calcola_punti_giornata_conn(row_get(g, 'giornata'), conn)

        # Ricalcola fasi knockout
        for fase in ['r32', 'r16', 'qf', 'sf', 'finale', '3posto']:
            partite = db_fetchall(conn,
                                  'SELECT * FROM partite WHERE fase=? AND vincitore IS NOT NULL',
                                  (fase,))
            if not partite:
                continue
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
                    calcola_punti_eliminazione(pron_idx.get((uid, pid)), parte_idx[pid])['totale']
                    for pid in pids
                )
                _upsert_punteggio_fase(conn, uid, fase, punti)

        # Bonus torneo
        rf = db_fetchone(conn, 'SELECT * FROM risultati_torneo WHERE id=1')
        if rf and row_get(rf, 'vincitore'):
            for u in db_fetchall(conn, 'SELECT id FROM utenti'):
                uid  = row_get(u, 'id')
                pron = db_fetchone(conn,
                                   'SELECT * FROM pronostici_torneo WHERE id_utente=?', (uid,))
                det  = calcola_punti_torneo(pron, rf)
                _upsert_punteggio_fase(conn, uid, 'torneo', det['totale'])

        _refresh_totali_tutti(conn)
        db_commit(conn)
    return 'Classifica completa ricalcolata!'
