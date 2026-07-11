"""
Logica di gioco — estratta da app.py per separazione delle responsabilità.

Espone:
  - parse_flexible_datetime()
  - calcola_punti_pronostico()        ← unica fonte di verità sulla formula
  - calcola_e_aggiorna_punti_giornata()
  - ricalcola_punteggi_totali()
  - ricalcola_punteggi_finali()
"""

import logging
import re
import unicodedata
from datetime import datetime

import pytz

from db_utils import db_conn, db_execute, db_fetchone, db_fetchall, db_commit, row_get, USE_POSTGRES

log = logging.getLogger('fanta')

# ─── Costanti di punteggio ────────────────────────────────────────────────────
PUNTI_ESITO       = 1
PUNTI_RISULTATO   = 3
PUNTI_MARCATORE   = 2
PUNTI_BONUS_TRIPLA = 1

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


# ─── Date / timezone ─────────────────────────────────────────────────────────

def parse_flexible_datetime(date_string: str) -> datetime | None:
    if not date_string:
        return None
    s = str(date_string).strip()
    # Normalizza il fuso ISO 8601 restituito dall'API (es. "2026-08-24T18:30:00Z"
    # o "...+00:00") in un datetime naive in UTC, che poi viene convertito a Roma.
    if s.endswith('Z'):
        s = s[:-1]
    s = re.sub(r'[+-]\d{2}:\d{2}$', '', s)
    for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M',
                '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def is_partita_scaduta(data_ora_utc_str: str | None) -> bool:
    """True se l'orario UTC della partita è già passato (ora Europa/Roma)."""
    if not data_ora_utc_str:
        return False
    orario_naive = parse_flexible_datetime(str(data_ora_utc_str))
    if not orario_naive:
        return False
    roma_tz = pytz.timezone('Europe/Rome')
    ora_corrente = datetime.now(roma_tz)
    return ora_corrente > pytz.utc.localize(orario_naive).astimezone(roma_tz)


# ─── Calcolo punti ────────────────────────────────────────────────────────────

def calcola_punti_pronostico(pronostico, partita) -> dict:
    """
    Calcola il dettaglio dei punti per un pronostico dato una partita conclusa.

    Parametri:
        pronostico  — sqlite3.Row / psycopg2 Row / dict / None
        partita     — sqlite3.Row / psycopg2 Row / dict

    Ritorna un dict con chiavi:
        esito, risultato, marcatore, bonus, totale  (int)
        esito_corretto, risultato_corretto, marcatore_corretto  (bool)
    """
    out = {
        'esito': 0, 'risultato': 0, 'marcatore': 0, 'bonus': 0, 'totale': 0,
        'esito_corretto': False,
        'risultato_corretto': False,
        'marcatore_corretto': False,
    }
    if not pronostico:
        return out

    r_casa = row_get(partita, 'risultato_casa_reale')
    r_osp  = row_get(partita, 'risultato_ospite_reale')
    if r_casa is None or r_osp is None:
        return out

    esito_reale = '1' if r_casa > r_osp else 'X' if r_casa == r_osp else '2'

    # Esito 1/X/2
    if row_get(pronostico, 'esito_pronosticato') == esito_reale:
        out['esito'] = PUNTI_ESITO
        out['esito_corretto'] = True

    # Risultato esatto
    if (row_get(pronostico, 'risultato_casa_pronosticato') == r_casa
            and row_get(pronostico, 'risultato_ospite_pronosticato') == r_osp):
        out['risultato'] = PUNTI_RISULTATO
        out['risultato_corretto'] = True

    # Marcatore
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

    # Bonus tripla
    if out['esito_corretto'] and out['risultato_corretto'] and out['marcatore_corretto']:
        out['bonus'] = PUNTI_BONUS_TRIPLA

    out['totale'] = (out['esito'] + out['risultato']
                     + out['marcatore'] + out['bonus'])
    return out


def _upsert_punteggio_giornata(conn, id_utente: int, giornata: int, punti: int):
    """UPSERT idempotente su punteggi_giornata."""
    if USE_POSTGRES:
        db_execute(
            conn,
            'INSERT INTO punteggi_giornata (id_utente, giornata, punti) '
            'VALUES (?, ?, ?) '
            'ON CONFLICT (id_utente, giornata) DO UPDATE SET punti = EXCLUDED.punti',
            (id_utente, giornata, punti),
        )
    else:
        db_execute(
            conn,
            'INSERT INTO punteggi_giornata (id_utente, giornata, punti) '
            'VALUES (?, ?, ?) '
            'ON CONFLICT (id_utente, giornata) DO UPDATE SET punti = excluded.punti',
            (id_utente, giornata, punti),
        )


def _refresh_totale_utente(conn, id_utente: int):
    """Ricalcola punteggi.punteggio_totale dalla somma di punteggi_giornata."""
    row = db_fetchone(
        conn,
        'SELECT COALESCE(SUM(punti), 0) AS tot FROM punteggi_giornata '
        'WHERE id_utente = ?',
        (id_utente,),
    )
    totale = row_get(row, 'tot') or 0
    if USE_POSTGRES:
        db_execute(
            conn,
            'INSERT INTO punteggi (id_utente, punteggio_totale) VALUES (?, ?) '
            'ON CONFLICT (id_utente) DO UPDATE SET punteggio_totale = EXCLUDED.punteggio_totale',
            (id_utente, totale),
        )
    else:
        db_execute(
            conn,
            'INSERT INTO punteggi (id_utente, punteggio_totale) VALUES (?, ?) '
            'ON CONFLICT (id_utente) DO UPDATE SET punteggio_totale = excluded.punteggio_totale',
            (id_utente, totale),
        )


def _calcola_punti_giornata_conn(giornata: int, conn) -> None:
    """Calcola e persiste i punti della giornata. IDEMPOTENTE."""
    utenti   = db_fetchall(conn, 'SELECT id FROM utenti')
    partite  = db_fetchall(
        conn,
        'SELECT * FROM partite WHERE giornata = ? AND pronosticabile = TRUE '
        'AND risultato_casa_reale IS NOT NULL',
        (giornata,),
    )
    if not partite:
        return

    pids = [row_get(p, 'id') for p in partite]
    placeholder = ','.join(['?'] * len(pids))
    pronostici_raw = db_fetchall(
        conn,
        f'SELECT * FROM pronostici_giornata WHERE id_partita IN ({placeholder})',
        tuple(pids),
    )
    pronostici_idx = {
        (row_get(p, 'id_utente'), row_get(p, 'id_partita')): p
        for p in pronostici_raw
    }
    partite_idx = {row_get(p, 'id'): p for p in partite}

    for utente in utenti:
        uid = row_get(utente, 'id')
        punti_giornata = sum(
            calcola_punti_pronostico(
                pronostici_idx.get((uid, pid)),
                partite_idx[pid],
            )['totale']
            for pid in pids
        )
        _upsert_punteggio_giornata(conn, uid, giornata, punti_giornata)
        _refresh_totale_utente(conn, uid)


def calcola_e_aggiorna_punti_giornata(giornata: int) -> str:
    with db_conn() as conn:
        check = db_fetchall(
            conn,
            'SELECT id FROM partite WHERE giornata = ? AND pronosticabile = TRUE '
            'AND risultato_casa_reale IS NOT NULL',
            (giornata,),
        )
        if not check:
            return f'Nessuna partita con risultati trovata per la giornata {giornata}.'
        _calcola_punti_giornata_conn(giornata, conn)
        db_commit(conn)
    return f'Punti per la Giornata {giornata} calcolati con successo!'


def ricalcola_punteggi_totali() -> str:
    """Ricalcola tutti i punteggi da zero. Idempotente."""
    with db_conn() as conn:
        db_execute(conn, 'DELETE FROM punteggi_giornata')
        db_execute(conn, 'DELETE FROM punteggi')
        for utente in db_fetchall(conn, 'SELECT id FROM utenti'):
            db_execute(
                conn,
                'INSERT INTO punteggi (id_utente, punteggio_totale) VALUES (?, 0)',
                (row_get(utente, 'id'),),
            )
        for g in db_fetchall(
            conn,
            'SELECT giornata FROM stato_giornata WHERE is_in_archivio = TRUE',
        ):
            _calcola_punti_giornata_conn(row_get(g, 'giornata'), conn)
        db_commit(conn)
    return 'Classifica generale ricalcolata con successo.'


def ricalcola_punteggi_finali() -> str:
    """Ricalcola totali stagione + bonus pronostici iniziali. Idempotente."""
    with db_conn() as conn:
        rf = db_fetchone(conn, 'SELECT * FROM risultati_finali WHERE id = 1')
        if not rf or not row_get(rf, 'squadra_1'):
            return 'Errore: inserire prima i risultati reali di fine stagione.'

    ricalcola_punteggi_totali()

    with db_conn() as conn:
        rf = db_fetchone(conn, 'SELECT * FROM risultati_finali WHERE id = 1')
        for utente in db_fetchall(conn, 'SELECT id FROM utenti'):
            uid   = row_get(utente, 'id')
            pron  = db_fetchone(
                conn,
                'SELECT * FROM pronostici_iniziali WHERE id_utente = ?',
                (uid,),
            )
            if not pron:
                continue
            punti, corrette = 0, 0
            for i in range(1, 5):
                k = f'squadra_{i}'
                if ((row_get(pron, k) or '').strip().lower()
                        == (row_get(rf, k) or '').strip().lower()):
                    punti += 20
                    corrette += 1
            if corrette == 4:
                punti += 10
            if ((row_get(pron, 'capocannoniere') or '').strip().lower()
                    == (row_get(rf, 'capocannoniere') or '').strip().lower()):
                punti += 20
            if punti:
                db_execute(
                    conn,
                    'UPDATE punteggi SET punteggio_totale = punteggio_totale + ? '
                    'WHERE id_utente = ?',
                    (punti, uid),
                )
        db_commit(conn)
    return 'Punti finali di stagione calcolati con successo!'


# ─── Matching nomi squadre (import risultati/partite) ──────────────────────────

_STOPWORDS_SQUADRA = {
    'FC', 'AC', 'AS', 'SS', 'SSC', 'US', 'ACF', 'CFC', 'BC', 'CALCIO',
}


def normalizza_squadra(nome: str) -> str:
    """Normalizza un nome squadra per il confronto:
    rimuove accenti, punteggiatura, parole comuni (FC, AC, Calcio...) e case."""
    if not nome:
        return ''
    s = unicodedata.normalize('NFKD', str(nome)).encode('ascii', 'ignore').decode()
    s = re.sub(r'[^A-Za-z0-9 ]', ' ', s).upper()
    tokens = [t for t in s.split() if t and t not in _STOPWORDS_SQUADRA]
    return ''.join(tokens)


def squadre_compatibili(a: str, b: str) -> bool:
    """True se due nomi squadra si riferiscono verosimilmente alla stessa squadra.
    Confronto su forma normalizzata (accenti/suffissi rimossi) con match per
    uguaglianza o inclusione, così da gestire abbreviazioni (es. INTER vs
    FC Internazionale Milano) riducendo i falsi positivi dei suffissi comuni."""
    na = normalizza_squadra(a)
    nb = normalizza_squadra(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na
