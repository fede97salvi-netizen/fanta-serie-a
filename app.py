"""
FantaSerieA — versione 2 (consolidata)
─────────────────────────────────────────────────────────────
Cambiamenti principali rispetto alla v1:

SICUREZZA
- Password: werkzeug.security (PBKDF2). Migrazione trasparente dai vecchi
  hash SHA-256 al primo login riuscito (l'utente non si accorge di nulla).
- Secret key: nessun fallback debole. Se manca SECRET_KEY in env si rifiuta
  di partire in produzione; in locale viene generata e persistita una volta sola.
- Ruolo admin: nuova colonna `is_admin` su `utenti`. L'utente storico 'mirko'
  viene promosso al primo avvio (migrazione automatica una tantum).
- CSRF: Flask-WTF abilitato globalmente. Token in tutti i form.
- Rate limit sul login: Flask-Limiter (5 tentativi/minuto/IP).
- Validazione minima su password (lunghezza), email (formato), risultati (range).
- Tutte le operazioni distruttive che erano via GET ora richiedono POST + CSRF.

LOGICA DI GIOCO
- Nuova tabella `punteggi_giornata(id_utente, giornata, punti)` con UNIQUE
  (id_utente, giornata). `punteggi.punteggio_totale` resta come somma cached,
  ma il calcolo è IDEMPOTENTE: calcolare la stessa giornata 10 volte produce
  lo stesso risultato. Niente più doppi conteggi.
- Estratta `calcola_punti_pronostico(pronostico, partita)` come UNICA fonte
  della formula. Sia la pagina /giornata che il calcolo persistente la usano.
- `ricalcola_punteggi_finali` è ora idempotente: resetta i totali, somma le
  giornate dalla tabella per-giornata, poi aggiunge il bonus di stagione.

PULIZIA
- Gestione connessioni DB con context manager → niente leak su eccezioni.
- Rimossi import morti (smtplib, MIMEMultipart duplicato).
- Eccezioni mute (`except: pass`) sostituite con logging.
- `app.run(debug=...)` controllato da env var FLASK_DEBUG.
- Validazione input dove serve.
"""

import os
import sys
import secrets
import logging
import threading
import random
import string
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256

import pytz
import requests as http_requests
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, abort
)
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf.csrf import CSRFProtect, generate_csrf
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address


# ═════════════════════════════════════════════════════════════
# CONFIGURAZIONE
# ═════════════════════════════════════════════════════════════

logging.basicConfig(
    level=os.environ.get('LOG_LEVEL', 'INFO'),
    format='[%(asctime)s] %(levelname)s %(name)s — %(message)s'
)
log = logging.getLogger('fanta')

# --- Database: SQLite in locale, PostgreSQL in produzione ---
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
if DATABASE_URL:
    import psycopg2
    import psycopg2.extras
    USE_POSTGRES = True
else:
    import sqlite3
    USE_POSTGRES = False

# --- Secret key: nessun fallback debole ---
def _load_secret_key():
    key = os.environ.get('SECRET_KEY')
    if key:
        return key
    # In produzione SECRET_KEY DEVE essere settata
    if os.environ.get('RENDER') or DATABASE_URL:
        log.error("SECRET_KEY non impostata in produzione. Esco.")
        sys.exit(1)
    # In locale: genera e persiste in un file ignorato da git
    keyfile = os.path.join(os.path.dirname(__file__), '.local_secret_key')
    if os.path.exists(keyfile):
        with open(keyfile) as f:
            return f.read().strip()
    new_key = secrets.token_hex(32)
    with open(keyfile, 'w') as f:
        f.write(new_key)
    log.warning("Generata nuova SECRET_KEY locale in .local_secret_key (gitignored).")
    return new_key


app = Flask(__name__)
app.secret_key = _load_secret_key()
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
# Cookie Secure solo se siamo dietro HTTPS (Render lo gestisce automaticamente)
if os.environ.get('RENDER') or DATABASE_URL:
    app.config['SESSION_COOKIE_SECURE'] = True
app.config['WTF_CSRF_TIME_LIMIT'] = None  # token valido per tutta la sessione

csrf = CSRFProtect(app)

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=[],  # niente default, applichiamo per-route dove serve
    storage_uri="memory://",
)

# --- Config email / API esterne ---
FOOTBALL_API_KEY = os.environ.get('FOOTBALL_API_KEY', '')
FOOTBALL_API_BASE = 'https://api.football-data.org/v4'
SERIE_A_CODE = 'SA'

RESEND_API_KEY = os.environ.get('RESEND_API_KEY', '')
EMAIL_FROM_NAME = 'FantaSerieA'
EMAIL_FROM_ADDRESS = os.environ.get('EMAIL_FROM_ADDRESS', 'onboarding@resend.dev')
APP_URL = os.environ.get('APP_URL', 'https://fanta-serie-a-1.onrender.com')


# ═════════════════════════════════════════════════════════════
# DATABASE — connessione, context manager, helper
# ═════════════════════════════════════════════════════════════

def _new_connection():
    if USE_POSTGRES:
        return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    conn = sqlite3.connect(os.path.join(app.root_path, 'database.db'), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def db_conn():
    """Context manager: chiude SEMPRE la connessione, anche su eccezione."""
    conn = _new_connection()
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            log.exception("Errore chiusura connessione DB")


def db_execute(conn, query, params=()):
    """Esegue una query adattando i placeholder (? per SQLite, %s per PostgreSQL)."""
    if USE_POSTGRES:
        query = query.replace('?', '%s')
        query = query.replace('INTEGER PRIMARY KEY AUTOINCREMENT', 'SERIAL PRIMARY KEY')
        query = query.replace('ON CONFLICT(id_utente) DO NOTHING', 'ON CONFLICT (id_utente) DO NOTHING')
    cur = conn.cursor()
    cur.execute(query, params)
    return cur


def db_fetchone(conn, query, params=()):
    return db_execute(conn, query, params).fetchone()


def db_fetchall(conn, query, params=()):
    return db_execute(conn, query, params).fetchall()


def db_commit(conn):
    conn.commit()


def row_get(row, key):
    """Accede a una riga sia da sqlite3.Row che da psycopg2 RealDictRow."""
    if row is None:
        return None
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


# ═════════════════════════════════════════════════════════════
# SCHEMA + MIGRAZIONI
# ═════════════════════════════════════════════════════════════

def _create_tables_postgres(conn):
    db_execute(conn, """CREATE TABLE IF NOT EXISTS utenti (
        id SERIAL PRIMARY KEY,
        nome_utente TEXT NOT NULL UNIQUE,
        password TEXT NOT NULL,
        is_temp_password BOOLEAN NOT NULL DEFAULT FALSE,
        is_admin BOOLEAN NOT NULL DEFAULT FALSE,
        email TEXT)""")
    db_execute(conn, """CREATE TABLE IF NOT EXISTS pronostici_iniziali (
        id SERIAL PRIMARY KEY,
        id_utente INTEGER NOT NULL REFERENCES utenti(id),
        squadra_1 TEXT, squadra_2 TEXT, squadra_3 TEXT, squadra_4 TEXT,
        capocannoniere TEXT)""")
    db_execute(conn, """CREATE TABLE IF NOT EXISTS partite (
        id SERIAL PRIMARY KEY,
        giornata INTEGER NOT NULL,
        squadra_casa TEXT NOT NULL, squadra_ospite TEXT NOT NULL,
        risultato_casa_reale INTEGER, risultato_ospite_reale INTEGER,
        marcatore_reale TEXT,
        pronosticabile BOOLEAN NOT NULL DEFAULT FALSE,
        data_ora_partita TEXT)""")
    db_execute(conn, """CREATE TABLE IF NOT EXISTS pronostici_giornata (
        id SERIAL PRIMARY KEY,
        id_utente INTEGER NOT NULL REFERENCES utenti(id),
        id_partita INTEGER NOT NULL REFERENCES partite(id),
        esito_pronosticato TEXT,
        risultato_casa_pronosticato INTEGER,
        risultato_ospite_pronosticato INTEGER,
        marcatore_pronosticato TEXT)""")
    db_execute(conn, """CREATE TABLE IF NOT EXISTS punteggi (
        id SERIAL PRIMARY KEY,
        id_utente INTEGER NOT NULL UNIQUE REFERENCES utenti(id),
        punteggio_totale INTEGER NOT NULL DEFAULT 0)""")
    db_execute(conn, """CREATE TABLE IF NOT EXISTS stato_giornata (
        id SERIAL PRIMARY KEY,
        giornata INTEGER NOT NULL UNIQUE,
        is_attiva BOOLEAN NOT NULL DEFAULT FALSE,
        is_in_archivio BOOLEAN NOT NULL DEFAULT FALSE)""")
    db_execute(conn, """CREATE TABLE IF NOT EXISTS stato_pronostici_iniziali (
        id INTEGER PRIMARY KEY,
        is_locked BOOLEAN NOT NULL DEFAULT FALSE)""")
    db_execute(conn, """CREATE TABLE IF NOT EXISTS risultati_finali (
        id INTEGER PRIMARY KEY,
        squadra_1 TEXT, squadra_2 TEXT, squadra_3 TEXT, squadra_4 TEXT,
        capocannoniere TEXT)""")
    db_execute(conn, """CREATE TABLE IF NOT EXISTS giocatori (
        id SERIAL PRIMARY KEY,
        nome_giocatore TEXT NOT NULL,
        squadra TEXT NOT NULL)""")
    # NUOVA tabella: punti per (utente, giornata) — idempotenza
    db_execute(conn, """CREATE TABLE IF NOT EXISTS punteggi_giornata (
        id SERIAL PRIMARY KEY,
        id_utente INTEGER NOT NULL REFERENCES utenti(id),
        giornata INTEGER NOT NULL,
        punti INTEGER NOT NULL DEFAULT 0,
        UNIQUE (id_utente, giornata))""")

    db_execute(conn, "INSERT INTO stato_pronostici_iniziali (id, is_locked) VALUES (1, FALSE) ON CONFLICT (id) DO NOTHING")
    db_execute(conn, "INSERT INTO risultati_finali (id) VALUES (1) ON CONFLICT (id) DO NOTHING")


def _create_tables_sqlite(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS utenti (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome_utente TEXT NOT NULL UNIQUE,
        password TEXT NOT NULL,
        is_temp_password BOOLEAN NOT NULL DEFAULT 0,
        is_admin BOOLEAN NOT NULL DEFAULT 0,
        email TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS pronostici_iniziali (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        id_utente INTEGER NOT NULL,
        squadra_1 TEXT, squadra_2 TEXT, squadra_3 TEXT, squadra_4 TEXT,
        capocannoniere TEXT,
        FOREIGN KEY(id_utente) REFERENCES utenti(id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS partite (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        giornata INTEGER NOT NULL,
        squadra_casa TEXT NOT NULL, squadra_ospite TEXT NOT NULL,
        risultato_casa_reale INTEGER, risultato_ospite_reale INTEGER,
        marcatore_reale TEXT,
        pronosticabile BOOLEAN NOT NULL DEFAULT 0,
        data_ora_partita TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS pronostici_giornata (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        id_utente INTEGER NOT NULL,
        id_partita INTEGER NOT NULL,
        esito_pronosticato TEXT,
        risultato_casa_pronosticato INTEGER,
        risultato_ospite_pronosticato INTEGER,
        marcatore_pronosticato TEXT,
        FOREIGN KEY(id_utente) REFERENCES utenti(id),
        FOREIGN KEY(id_partita) REFERENCES partite(id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS punteggi (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        id_utente INTEGER NOT NULL UNIQUE,
        punteggio_totale INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY(id_utente) REFERENCES utenti(id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS stato_giornata (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        giornata INTEGER NOT NULL UNIQUE,
        is_attiva BOOLEAN NOT NULL DEFAULT 0,
        is_in_archivio BOOLEAN NOT NULL DEFAULT 0)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS stato_pronostici_iniziali (
        id INTEGER PRIMARY KEY,
        is_locked BOOLEAN NOT NULL DEFAULT 0)""")
    conn.execute("INSERT OR IGNORE INTO stato_pronostici_iniziali (id, is_locked) VALUES (1, 0)")
    conn.execute("""CREATE TABLE IF NOT EXISTS risultati_finali (
        id INTEGER PRIMARY KEY,
        squadra_1 TEXT, squadra_2 TEXT, squadra_3 TEXT, squadra_4 TEXT,
        capocannoniere TEXT)""")
    conn.execute("INSERT OR IGNORE INTO risultati_finali (id) VALUES (1)")
    conn.execute("""CREATE TABLE IF NOT EXISTS giocatori (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome_giocatore TEXT NOT NULL,
        squadra TEXT NOT NULL)""")
    # NUOVA tabella: punti per (utente, giornata) — idempotenza
    conn.execute("""CREATE TABLE IF NOT EXISTS punteggi_giornata (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        id_utente INTEGER NOT NULL,
        giornata INTEGER NOT NULL,
        punti INTEGER NOT NULL DEFAULT 0,
        UNIQUE (id_utente, giornata),
        FOREIGN KEY(id_utente) REFERENCES utenti(id))""")


def _migrate_schema(conn):
    """Migrazioni idempotenti: aggiunge colonne mancanti su DB esistenti."""
    if USE_POSTGRES:
        try:
            db_execute(conn, "ALTER TABLE utenti ADD COLUMN IF NOT EXISTS email TEXT")
            db_execute(conn, "ALTER TABLE utenti ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE")
        except Exception:
            log.exception("Errore migrazione schema (postgres)")
    else:
        # SQLite non supporta IF NOT EXISTS sulle colonne, devo controllare PRAGMA
        cur = conn.execute("PRAGMA table_info(utenti)")
        cols = {r[1] for r in cur.fetchall()}
        if 'email' not in cols:
            try:
                conn.execute("ALTER TABLE utenti ADD COLUMN email TEXT")
            except Exception:
                log.exception("Errore aggiunta colonna email (sqlite)")
        if 'is_admin' not in cols:
            try:
                conn.execute("ALTER TABLE utenti ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0")
            except Exception:
                log.exception("Errore aggiunta colonna is_admin (sqlite)")


def _promuovi_admin_storico(conn):
    """Se nessun utente è admin e esiste 'mirko', lo promuove (una tantum)."""
    row = db_fetchone(conn, "SELECT COUNT(*) AS c FROM utenti WHERE is_admin = TRUE" if USE_POSTGRES
                     else "SELECT COUNT(*) AS c FROM utenti WHERE is_admin = 1")
    count = row_get(row, 'c') or 0
    if count == 0:
        legacy_admin = os.environ.get('LEGACY_ADMIN_USERNAME', 'mirko')
        if USE_POSTGRES:
            db_execute(conn, "UPDATE utenti SET is_admin = TRUE WHERE nome_utente = ?", (legacy_admin,))
        else:
            db_execute(conn, "UPDATE utenti SET is_admin = 1 WHERE nome_utente = ?", (legacy_admin,))
        log.info(f"Migrazione: promosso '{legacy_admin}' ad admin (se esisteva).")


def create_tables():
    with db_conn() as conn:
        if USE_POSTGRES:
            _create_tables_postgres(conn)
        else:
            _create_tables_sqlite(conn)
        _migrate_schema(conn)
        _promuovi_admin_storico(conn)
        db_commit(conn)


# ═════════════════════════════════════════════════════════════
# AUTH — hashing password, sessione, admin
# ═════════════════════════════════════════════════════════════

# Lunghezza minima password
MIN_PASSWORD_LEN = 6  # tieni basso per non rompere account esistenti; raccomando ≥8 per i nuovi
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _is_legacy_sha256(pw_hash: str) -> bool:
    """Vecchio formato: 64 caratteri esadecimali, no prefisso werkzeug."""
    return bool(pw_hash) and len(pw_hash) == 64 and all(c in '0123456789abcdef' for c in pw_hash.lower())


def verifica_password(plain: str, stored_hash: str) -> bool:
    """Verifica la password supportando sia il nuovo formato che il vecchio SHA-256."""
    if not stored_hash:
        return False
    if _is_legacy_sha256(stored_hash):
        return sha256(plain.encode()).hexdigest() == stored_hash
    try:
        return check_password_hash(stored_hash, plain)
    except Exception:
        log.exception("Errore verifica password")
        return False


def hash_password(plain: str) -> str:
    return generate_password_hash(plain, method='pbkdf2:sha256', salt_length=16)


def utente_corrente(conn):
    """Carica l'utente dalla sessione (o None)."""
    if 'nome_utente' not in session:
        return None
    return db_fetchone(conn, "SELECT * FROM utenti WHERE nome_utente = ?", (session['nome_utente'],))


def is_admin_session() -> bool:
    """True se l'utente in sessione è admin. Lettura veloce dalla sessione cached."""
    return bool(session.get('is_admin'))


def require_admin():
    """Compatibilità con la vecchia signature: ritorna True se NON admin (= accesso negato)."""
    return 'nome_utente' not in session or not is_admin_session()


# ═════════════════════════════════════════════════════════════
# CONTEXT PROCESSOR — giornata attiva globale + csrf nei template
# ═════════════════════════════════════════════════════════════

@app.context_processor
def inject_globals():
    g_attiva = None
    try:
        with db_conn() as conn:
            row = db_fetchone(conn, "SELECT giornata FROM stato_giornata WHERE is_attiva = TRUE")
            g_attiva = row_get(row, 'giornata') if row else None
    except Exception:
        log.exception("Errore lettura giornata attiva")
    return {
        'giornata_attiva': g_attiva,
        'csrf_token': generate_csrf,  # nei template: {{ csrf_token() }}
        'is_admin': is_admin_session(),
    }


# ═════════════════════════════════════════════════════════════
# UTILITY DATE
# ═════════════════════════════════════════════════════════════

def parse_flexible_datetime(date_string):
    if not date_string:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(date_string, fmt)
        except ValueError:
            continue
    return None


@app.template_filter('datetime_local_italia')
def datetime_local_italia(data_ora_utc_str):
    if not data_ora_utc_str:
        return ""
    try:
        orario_naive = parse_flexible_datetime(str(data_ora_utc_str))
        if not orario_naive:
            return str(data_ora_utc_str)
        roma_tz = pytz.timezone('Europe/Rome')
        return pytz.utc.localize(orario_naive).astimezone(roma_tz).strftime("%Y-%m-%dT%H:%M")
    except Exception:
        return str(data_ora_utc_str)


@app.template_filter('fuso_orario_italia')
def fuso_orario_italia(data_ora_utc_str):
    if not data_ora_utc_str:
        return ""
    try:
        orario_naive = parse_flexible_datetime(str(data_ora_utc_str))
        if not orario_naive:
            return str(data_ora_utc_str)
        roma_tz = pytz.timezone('Europe/Rome')
        return pytz.utc.localize(orario_naive).astimezone(roma_tz).strftime("%d/%m/%Y %H:%M")
    except Exception:
        return str(data_ora_utc_str)


# ═════════════════════════════════════════════════════════════
# EMAIL (Resend API)
# ═════════════════════════════════════════════════════════════

def invia_email_async(destinatari, oggetto, corpo_html):
    """Invia email in background senza bloccare la richiesta HTTP."""
    def _invia():
        try:
            log.info(f"[EMAIL] Avvio invio a {len(destinatari)} destinatari...")
            successi, errori = invia_email(destinatari, oggetto, corpo_html)
            log.info(f"[EMAIL] Completato: {successi} successi, {len(errori)} errori")
            for e in errori:
                log.warning(f"[EMAIL] Errore: {e}")
        except Exception:
            log.exception("Eccezione nel thread email")
    threading.Thread(target=_invia, daemon=True).start()


def invia_email(destinatari, oggetto, corpo_html):
    """Invia email tramite Resend API. Restituisce (successi, errori)."""
    if not RESEND_API_KEY:
        return 0, ["Email non configurata (RESEND_API_KEY mancante)"]
    successi = 0
    errori = []
    for dest in destinatari:
        try:
            r = http_requests.post(
                'https://api.resend.com/emails',
                headers={
                    'Authorization': f'Bearer {RESEND_API_KEY}',
                    'Content-Type': 'application/json'
                },
                json={
                    'from': f'{EMAIL_FROM_NAME} <{EMAIL_FROM_ADDRESS}>',
                    'to': [dest],
                    'subject': oggetto,
                    'html': corpo_html
                },
                timeout=15
            )
            if r.status_code in (200, 201):
                successi += 1
                log.info(f"[EMAIL] Inviata a {dest}")
            else:
                try:
                    msg = r.json().get('message', r.text[:100])
                except Exception:
                    msg = r.text[:100]
                errori.append(f"{dest}: {msg}")
                log.warning(f"[EMAIL] Errore per {dest}: {msg}")
        except Exception as e:
            errori.append(f"{dest}: {str(e)}")
            log.exception(f"[EMAIL] Eccezione per {dest}")
    return successi, errori


def converti_data_email(data_ora_utc_str):
    if not data_ora_utc_str:
        return 'Data da definire'
    try:
        roma_tz = pytz.timezone('Europe/Rome')
        orario_naive = parse_flexible_datetime(str(data_ora_utc_str))
        if orario_naive is None:
            return str(data_ora_utc_str)
        return pytz.utc.localize(orario_naive).astimezone(roma_tz).strftime("%d/%m/%Y alle %H:%M")
    except Exception:
        log.exception("Errore conversione data email")
        return str(data_ora_utc_str)


def build_email_giornata(giornata, partite):
    partite_html = ""
    for p in partite:
        data_str = converti_data_email(p.get('data_ora_partita') or '')
        partite_html += f"""
        <tr><td style="padding:12px 16px;border-bottom:1px solid #e5e7eb;">
          <strong style="font-size:16px;color:#1e3a5f;">{p['squadra_casa']} vs {p['squadra_ospite']}</strong>
          <div style="font-size:13px;color:#6b7280;margin-top:4px;">📅 {data_str}</div>
        </td></tr>"""
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:32px 16px;">
    <tr><td align="center">
      <table width="100%" style="max-width:520px;background:white;border-radius:16px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08);">
        <tr><td style="background:linear-gradient(135deg,#003f8a,#0f4a1e);padding:28px 24px;text-align:center;">
          <div style="font-size:32px;margin-bottom:8px;">🏆</div>
          <h1 style="color:white;margin:0;font-size:24px;letter-spacing:1px;">FantaSerieA</h1>
          <p style="color:rgba(255,255,255,0.8);margin:8px 0 0;font-size:14px;">Giornata {giornata} — Inserisci i tuoi pronostici!</p>
        </td></tr>
        <tr><td style="padding:24px;">
          <p style="color:#374151;font-size:15px;margin:0 0 16px;">Le partite della <strong>giornata {giornata}</strong> sono pronte.</p>
          <h2 style="color:#1e3a5f;font-size:16px;margin:0 0 12px;text-transform:uppercase;letter-spacing:1px;">Le 3 partite da pronosticare</h2>
          <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e5e7eb;border-radius:10px;overflow:hidden;">{partite_html}</table>
          <div style="text-align:center;margin-top:24px;">
            <a href="{APP_URL}/pronostici-giornata/{giornata}" style="background:linear-gradient(135deg,#1565c0,#0090d4);color:white;padding:12px 32px;border-radius:8px;text-decoration:none;font-weight:bold;font-size:15px;display:inline-block;">Inserisci i pronostici →</a>
          </div>
          <p style="color:#9ca3af;font-size:12px;text-align:center;margin-top:24px;">Ricevi questa email perché sei iscritto a FantaSerieA.<br><a href="{APP_URL}" style="color:#0090d4;">Vai all'app</a></p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""


def build_email_pronostici(giornata, partite):
    """Backward-compatible: alias di build_email_giornata."""
    return build_email_giornata(giornata, partite)


# ═════════════════════════════════════════════════════════════
# LOGICA DI GIOCO — UNICA fonte di verità per il calcolo punti
# ═════════════════════════════════════════════════════════════

PUNTI_ESITO = 1
PUNTI_RISULTATO = 3
PUNTI_MARCATORE = 2
PUNTI_BONUS_TRIPLA = 1


def calcola_punti_pronostico(pronostico, partita):
    """
    Calcola il dettaglio dei punti per un pronostico data una partita conclusa.

    Ritorna un dict con chiavi: 'esito', 'risultato', 'marcatore', 'bonus', 'totale',
    e bool 'esito_corretto', 'risultato_corretto', 'marcatore_corretto'.

    Se il pronostico non c'è o la partita non ha risultato, restituisce tutti zeri.
    """
    out = {
        'esito': 0, 'risultato': 0, 'marcatore': 0, 'bonus': 0, 'totale': 0,
        'esito_corretto': False, 'risultato_corretto': False, 'marcatore_corretto': False,
    }
    if not pronostico:
        return out
    r_casa = row_get(partita, 'risultato_casa_reale')
    r_osp = row_get(partita, 'risultato_ospite_reale')
    if r_casa is None or r_osp is None:
        return out

    esito_reale = "1" if r_casa > r_osp else "X" if r_casa == r_osp else "2"

    # Esito (1/X/2)
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
    if pm == "nessun marcatore":
        if r_casa == 0 and r_osp == 0:
            out['marcatore'] = PUNTI_MARCATORE
            out['marcatore_corretto'] = True
    elif pm and pm in marcatori_reali:
        out['marcatore'] = PUNTI_MARCATORE
        out['marcatore_corretto'] = True

    # Bonus tripla
    if out['esito_corretto'] and out['risultato_corretto'] and out['marcatore_corretto']:
        out['bonus'] = PUNTI_BONUS_TRIPLA

    out['totale'] = out['esito'] + out['risultato'] + out['marcatore'] + out['bonus']
    return out


def _upsert_punteggio_giornata(conn, id_utente, giornata, punti):
    """UPSERT su punteggi_giornata. Idempotente."""
    if USE_POSTGRES:
        db_execute(conn,
                   "INSERT INTO punteggi_giornata (id_utente, giornata, punti) VALUES (?, ?, ?) "
                   "ON CONFLICT (id_utente, giornata) DO UPDATE SET punti = EXCLUDED.punti",
                   (id_utente, giornata, punti))
    else:
        # SQLite supporta ON CONFLICT da 3.24 (2018+, ovunque dove gira Python moderno)
        db_execute(conn,
                   "INSERT INTO punteggi_giornata (id_utente, giornata, punti) VALUES (?, ?, ?) "
                   "ON CONFLICT (id_utente, giornata) DO UPDATE SET punti = excluded.punti",
                   (id_utente, giornata, punti))


def _refresh_totale_utente(conn, id_utente):
    """Ricalcola punteggi.punteggio_totale dell'utente dalla somma di punteggi_giornata."""
    row = db_fetchone(conn,
                      "SELECT COALESCE(SUM(punti), 0) AS tot FROM punteggi_giornata WHERE id_utente = ?",
                      (id_utente,))
    totale = row_get(row, 'tot') or 0
    if USE_POSTGRES:
        db_execute(conn,
                   "INSERT INTO punteggi (id_utente, punteggio_totale) VALUES (?, ?) "
                   "ON CONFLICT (id_utente) DO UPDATE SET punteggio_totale = EXCLUDED.punteggio_totale",
                   (id_utente, totale))
    else:
        db_execute(conn,
                   "INSERT INTO punteggi (id_utente, punteggio_totale) VALUES (?, ?) "
                   "ON CONFLICT (id_utente) DO UPDATE SET punteggio_totale = excluded.punteggio_totale",
                   (id_utente, totale))


def _calcola_punti_giornata_conn(giornata, conn):
    """
    Calcola e PERSISTE i punti della giornata indicata.
    IDEMPOTENTE: chiamare 10 volte = chiamare 1 volta.
    """
    utenti = db_fetchall(conn, "SELECT id FROM utenti")
    partite = db_fetchall(conn,
                          "SELECT * FROM partite WHERE giornata = ? AND pronosticabile = TRUE AND risultato_casa_reale IS NOT NULL",
                          (giornata,))
    if not partite:
        return

    # Carico tutti i pronostici della giornata in un solo round-trip (no N+1)
    pids = [row_get(p, 'id') for p in partite]
    placeholder = ','.join(['?'] * len(pids))
    pronostici_raw = db_fetchall(
        conn,
        f"SELECT * FROM pronostici_giornata WHERE id_partita IN ({placeholder})",
        tuple(pids),
    )
    # Indice: (id_utente, id_partita) -> pronostico
    pronostici_idx = {(row_get(p, 'id_utente'), row_get(p, 'id_partita')): p for p in pronostici_raw}
    partite_idx = {row_get(p, 'id'): p for p in partite}

    for utente in utenti:
        uid = row_get(utente, 'id')
        punti_giornata = 0
        for pid in pids:
            pron = pronostici_idx.get((uid, pid))
            dettaglio = calcola_punti_pronostico(pron, partite_idx[pid])
            punti_giornata += dettaglio['totale']
        _upsert_punteggio_giornata(conn, uid, giornata, punti_giornata)
        _refresh_totale_utente(conn, uid)


def calcola_e_aggiorna_punti_giornata(giornata):
    with db_conn() as conn:
        partite_check = db_fetchall(conn,
                                    "SELECT id FROM partite WHERE giornata = ? AND pronosticabile = TRUE AND risultato_casa_reale IS NOT NULL",
                                    (giornata,))
        if not partite_check:
            return f"Nessuna partita con risultati trovata per la giornata {giornata}."
        _calcola_punti_giornata_conn(giornata, conn)
        db_commit(conn)
    return f"Punti per la Giornata {giornata} calcolati con successo!"


def ricalcola_punteggi_totali():
    """
    Ricalcola tutti i punteggi da zero scorrendo le giornate archiviate.
    Idempotente per costruzione.
    """
    with db_conn() as conn:
        # Reset completo
        db_execute(conn, "DELETE FROM punteggi_giornata")
        db_execute(conn, "DELETE FROM punteggi")
        utenti = db_fetchall(conn, "SELECT id FROM utenti")
        for utente in utenti:
            db_execute(conn,
                       "INSERT INTO punteggi (id_utente, punteggio_totale) VALUES (?, 0)",
                       (row_get(utente, 'id'),))
        giornate = db_fetchall(conn,
                               "SELECT giornata FROM stato_giornata WHERE is_in_archivio = TRUE")
        for g in giornate:
            _calcola_punti_giornata_conn(row_get(g, 'giornata'), conn)
        db_commit(conn)
    return "Classifica generale ricalcolata con successo."


def ricalcola_punteggi_finali():
    """Ricalcola totali stagione + bonus pronostici iniziali. Idempotente."""
    with db_conn() as conn:
        rf = db_fetchone(conn, "SELECT * FROM risultati_finali WHERE id = 1")
        if not rf or not row_get(rf, 'squadra_1'):
            return "Errore: inserire prima i risultati reali di fine stagione."

    # Prima ricalcolo i totali di tutte le giornate da zero
    ricalcola_punteggi_totali()

    # Poi aggiungo i bonus dei pronostici iniziali sopra ai totali
    with db_conn() as conn:
        rf = db_fetchone(conn, "SELECT * FROM risultati_finali WHERE id = 1")
        utenti = db_fetchall(conn, "SELECT id FROM utenti")
        for utente in utenti:
            uid = row_get(utente, 'id')
            pron = db_fetchone(conn, "SELECT * FROM pronostici_iniziali WHERE id_utente = ?", (uid,))
            if not pron:
                continue
            punti = 0
            corrette = 0
            for i in range(1, 5):
                k = f'squadra_{i}'
                if (row_get(pron, k) or '').strip().lower() == (row_get(rf, k) or '').strip().lower():
                    punti += 20
                    corrette += 1
            if corrette == 4:
                punti += 10
            if (row_get(pron, 'capocannoniere') or '').strip().lower() == (row_get(rf, 'capocannoniere') or '').strip().lower():
                punti += 20
            if punti:
                db_execute(conn,
                           "UPDATE punteggi SET punteggio_totale = punteggio_totale + ? WHERE id_utente = ?",
                           (punti, uid))
        db_commit(conn)
    return "Punti finali di stagione calcolati con successo!"


# ═════════════════════════════════════════════════════════════
# INTEGRAZIONE API FOOTBALL-DATA.ORG
# ═════════════════════════════════════════════════════════════

def api_headers():
    return {'X-Auth-Token': FOOTBALL_API_KEY}


def get_risultati_giornata(giornata):
    """Recupera i risultati di una giornata dalla API. Restituisce (lista_partite, errore_str)."""
    if not FOOTBALL_API_KEY:
        return None, "FOOTBALL_API_KEY non configurata."
    try:
        url = f"{FOOTBALL_API_BASE}/competitions/{SERIE_A_CODE}/matches"
        r = http_requests.get(url, headers=api_headers(),
                              params={'matchday': giornata}, timeout=15)
        if r.status_code != 200:
            return None, f"API risposta {r.status_code}: {r.text[:200]}"
        data = r.json()
        matches = data.get('matches', [])
        out = []
        for m in matches:
            if m.get('status') != 'FINISHED':
                continue
            home = (m['homeTeam']['name'] or '').upper()
            away = (m['awayTeam']['name'] or '').upper()
            gol_home = m['score']['fullTime']['home']
            gol_away = m['score']['fullTime']['away']
            marcatori = []
            # L'endpoint matches base non include scorers per piano free,
            # ma se ci sono li raccogliamo
            for s in (m.get('scorers') or []):
                if s.get('player', {}).get('name'):
                    marcatori.append(s['player']['name'])
            out.append({
                'home': home, 'away': away,
                'gol_home': gol_home, 'gol_away': gol_away,
                'marcatori_str': ', '.join(marcatori),
            })
        return out, None
    except Exception as e:
        log.exception("Errore chiamata API football-data")
        return None, str(e)


# ═════════════════════════════════════════════════════════════
# ROUTE PUBBLICHE: home, registrazione, login, logout
# ═════════════════════════════════════════════════════════════

@app.route("/")
def home():
    if 'nome_utente' not in session:
        return render_template("welcome.html", session=session)
    with db_conn() as conn:
        giornata_row = db_fetchone(conn, "SELECT giornata FROM stato_giornata WHERE is_attiva = TRUE")
        giornata_attiva = row_get(giornata_row, 'giornata') if giornata_row else None
        user = utente_corrente(conn)
        if not user:
            return redirect(url_for('logout'))
        user_id = row_get(user, 'id')
        punteggio_row = db_fetchone(conn, "SELECT punteggio_totale FROM punteggi WHERE id_utente = ?", (user_id,))
        punteggio_utente = row_get(punteggio_row, 'punteggio_totale') or 0
        posizione_row = db_fetchone(conn, "SELECT COUNT(id) + 1 as rank FROM punteggi WHERE punteggio_totale > ?", (punteggio_utente,))
        posizione_utente = row_get(posizione_row, 'rank') or 1
    return render_template("home.html", giornata_attiva=giornata_attiva,
                           punteggio_utente=punteggio_utente,
                           posizione_utente=posizione_utente, session=session)


@app.route("/registrazione", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"])
def registrazione():
    if request.method == "POST":
        nome_utente = (request.form.get("nome_utente") or "").strip()
        password = request.form.get("password") or ""
        if len(nome_utente) < 2:
            return render_template("registrazione.html", session=session,
                                   errore="Nome utente troppo corto.")
        if len(password) < MIN_PASSWORD_LEN:
            return render_template("registrazione.html", session=session,
                                   errore=f"La password deve avere almeno {MIN_PASSWORD_LEN} caratteri.")
        try:
            with db_conn() as conn:
                # Controllo esplicito per dare un errore chiaro
                exists = db_fetchone(conn, "SELECT id FROM utenti WHERE nome_utente = ?", (nome_utente,))
                if exists:
                    return render_template("registrazione.html", session=session,
                                           errore="Nome utente già esistente. Scegli un altro nome.")
                db_execute(conn,
                           "INSERT INTO utenti (nome_utente, password) VALUES (?, ?)",
                           (nome_utente, hash_password(password)))
                db_commit(conn)
            session['nome_utente'] = nome_utente
            session['is_admin'] = False
            return redirect(url_for("home"))
        except Exception:
            log.exception("Errore registrazione")
            return render_template("registrazione.html", session=session,
                                   errore="Errore durante la registrazione. Riprova.")
    return render_template("registrazione.html", session=session)


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute; 30 per hour", methods=["POST"])
def login():
    if request.method == "POST":
        nome_utente = (request.form.get("nome_utente") or "").strip()
        password = request.form.get("password") or ""
        with db_conn() as conn:
            user = db_fetchone(conn, "SELECT * FROM utenti WHERE nome_utente = ?", (nome_utente,))
            if not user or not verifica_password(password, row_get(user, 'password')):
                return render_template("login.html", session=session,
                                       errore="Credenziali non valide. Riprova.")
            # Migrazione trasparente: se l'hash era SHA-256 legacy, lo riconverto a PBKDF2
            if _is_legacy_sha256(row_get(user, 'password')):
                try:
                    db_execute(conn, "UPDATE utenti SET password = ? WHERE id = ?",
                               (hash_password(password), row_get(user, 'id')))
                    db_commit(conn)
                    log.info(f"Migrato hash password per utente {nome_utente}")
                except Exception:
                    log.exception("Errore upgrade hash password")
            if request.form.get('remember'):
                session.permanent = True
            session['nome_utente'] = nome_utente
            session['is_admin'] = bool(row_get(user, 'is_admin'))
            if row_get(user, 'is_temp_password'):
                return redirect(url_for('cambia_password'))
        return redirect(url_for("home"))
    return render_template("login.html", session=session)


@app.route("/cambia-password", methods=["GET", "POST"])
def cambia_password():
    if 'nome_utente' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        nuova_password = request.form.get('nuova_password') or ""
        conferma = request.form.get('conferma_password') or ""
        if nuova_password != conferma:
            return render_template('cambia_password.html', session=session,
                                   errore="Le password non coincidono.")
        if len(nuova_password) < MIN_PASSWORD_LEN:
            return render_template('cambia_password.html', session=session,
                                   errore=f"La password deve avere almeno {MIN_PASSWORD_LEN} caratteri.")
        with db_conn() as conn:
            db_execute(conn,
                       "UPDATE utenti SET password = ?, is_temp_password = FALSE WHERE nome_utente = ?",
                       (hash_password(nuova_password), session['nome_utente']))
            db_commit(conn)
        return redirect(url_for('home'))
    return render_template('cambia_password.html', session=session)


@app.route("/logout")
def logout():
    session.pop('nome_utente', None)
    session.pop('is_admin', None)
    return redirect(url_for("home"))


# ═════════════════════════════════════════════════════════════
# ROUTE GIOCATORE: classifica, archivio, giornata, pronostici
# ═════════════════════════════════════════════════════════════

@app.route("/classifica")
def classifica():
    if 'nome_utente' not in session:
        return redirect(url_for("login"))
    with db_conn() as conn:
        classifica_utenti = db_fetchall(conn,
            "SELECT u.nome_utente, p.punteggio_totale FROM utenti u "
            "JOIN punteggi p ON u.id = p.id_utente ORDER BY p.punteggio_totale DESC")
    return render_template("classifica.html", classifica=classifica_utenti, session=session)


@app.route("/giornate")
def archivio_giornate():
    if 'nome_utente' not in session:
        return redirect(url_for("login"))
    with db_conn() as conn:
        giornate = db_fetchall(conn,
            "SELECT * FROM stato_giornata WHERE is_in_archivio = TRUE ORDER BY giornata")
    return render_template("archivio_giornate.html", giornate=giornate, session=session)


@app.route("/giornata/<int:giornata>")
def visualizza_giornata(giornata):
    if 'nome_utente' not in session:
        return redirect(url_for("login"))
    with db_conn() as conn:
        partite_reali = db_fetchall(conn,
            "SELECT * FROM partite WHERE giornata = ? ORDER BY pronosticabile DESC, data_ora_partita",
            (giornata,))
        partite_pron = db_fetchall(conn,
            "SELECT * FROM partite WHERE giornata = ? AND pronosticabile = TRUE AND risultato_casa_reale IS NOT NULL",
            (giornata,))
        utenti = db_fetchall(conn, "SELECT id, nome_utente FROM utenti")

        # Pre-carico tutti i pronostici della giornata (no N+1)
        pids = [row_get(p, 'id') for p in partite_pron]
        pronostici_idx = {}
        if pids:
            placeholder = ','.join(['?'] * len(pids))
            rows = db_fetchall(conn,
                f"SELECT * FROM pronostici_giornata WHERE id_partita IN ({placeholder})",
                tuple(pids))
            pronostici_idx = {(row_get(r, 'id_utente'), row_get(r, 'id_partita')): r for r in rows}

        classifica_giornata = []
        for utente in utenti:
            uid = row_get(utente, 'id')
            punti_utente = 0
            punti_per_partita = {}
            for partita in partite_pron:
                pid = row_get(partita, 'id')
                pron = pronostici_idx.get((uid, pid))
                dettaglio = calcola_punti_pronostico(pron, partita)
                punti_per_partita[pid] = dettaglio
                punti_utente += dettaglio['totale']
            classifica_giornata.append({
                'nome_utente': row_get(utente, 'nome_utente'),
                'punti_totali': punti_utente,
                'punti_per_partita': punti_per_partita,
            })
        classifica_giornata.sort(key=lambda x: x['punti_totali'], reverse=True)

        # Pronostici per partita (chi ha messo cosa)
        pronostici_per_partita = {}
        if pids:
            placeholder = ','.join(['?'] * len(pids))
            rows = db_fetchall(conn,
                "SELECT u.nome_utente, pg.id_partita, pg.esito_pronosticato, "
                "pg.risultato_casa_pronosticato, pg.risultato_ospite_pronosticato, pg.marcatore_pronosticato "
                f"FROM pronostici_giornata pg JOIN utenti u ON pg.id_utente = u.id "
                f"WHERE pg.id_partita IN ({placeholder})",
                tuple(pids))
            for r in rows:
                pid = row_get(r, 'id_partita')
                pronostici_per_partita.setdefault(pid, {})[row_get(r, 'nome_utente')] = {
                    'esito': row_get(r, 'esito_pronosticato'),
                    'r_casa': row_get(r, 'risultato_casa_pronosticato'),
                    'r_osp': row_get(r, 'risultato_ospite_pronosticato'),
                    'marcatore': row_get(r, 'marcatore_pronosticato'),
                }

    return render_template("visualizza_giornata.html", giornata=giornata,
                           partite=partite_reali, partite_pron=partite_pron,
                           classifica=classifica_giornata,
                           pronostici_per_partita=pronostici_per_partita,
                           session=session)


@app.route("/pronostici-iniziali", methods=["GET", "POST"])
def pronostici_iniziali():
    if 'nome_utente' not in session:
        return redirect(url_for("login"))
    with db_conn() as conn:
        lock_row = db_fetchone(conn, "SELECT is_locked FROM stato_pronostici_iniziali WHERE id = 1")
        is_locked = row_get(lock_row, 'is_locked') if lock_row else True
        user = utente_corrente(conn)
        if not user:
            return redirect(url_for('logout'))
        user_id = row_get(user, 'id')
        if is_locked:
            pronostici_tutti = db_fetchall(conn,
                "SELECT u.nome_utente, pi.* FROM pronostici_iniziali pi "
                "JOIN utenti u ON pi.id_utente = u.id ORDER BY u.nome_utente")
            return render_template("pronostici_iniziali.html",
                                   is_locked=is_locked,
                                   pronostici_tutti=pronostici_tutti,
                                   session=session)
        if request.method == "POST":
            s1 = request.form.get("squadra_1") or ""
            s2 = request.form.get("squadra_2") or ""
            s3 = request.form.get("squadra_3") or ""
            s4 = request.form.get("squadra_4") or ""
            cc = request.form.get("capocannoniere") or ""
            esiste = db_fetchone(conn,
                "SELECT id FROM pronostici_iniziali WHERE id_utente = ?", (user_id,))
            if esiste:
                db_execute(conn,
                    "UPDATE pronostici_iniziali SET squadra_1=?, squadra_2=?, squadra_3=?, squadra_4=?, capocannoniere=? "
                    "WHERE id_utente=?", (s1, s2, s3, s4, cc, user_id))
            else:
                db_execute(conn,
                    "INSERT INTO pronostici_iniziali (id_utente, squadra_1, squadra_2, squadra_3, squadra_4, capocannoniere) "
                    "VALUES (?,?,?,?,?,?)", (user_id, s1, s2, s3, s4, cc))
            db_commit(conn)
            return redirect(url_for("home"))
        pronostico = db_fetchone(conn,
            "SELECT * FROM pronostici_iniziali WHERE id_utente = ?", (user_id,))
        return render_template("pronostici_iniziali.html",
                               is_locked=is_locked, pronostico=pronostico,
                               session=session)


def _safe_int(value, lo=None, hi=None):
    """Converte in int validando il range. Ritorna None se non valido."""
    if value is None or value == "":
        return None
    try:
        v = int(value)
    except (ValueError, TypeError):
        return None
    if lo is not None and v < lo:
        return None
    if hi is not None and v > hi:
        return None
    return v


@app.route("/pronostici-giornata/<int:giornata>", methods=["GET", "POST"])
def pronostici_giornata(giornata):
    if 'nome_utente' not in session:
        return redirect(url_for("login"))
    roma_tz = pytz.timezone('Europe/Rome')
    ora_corrente = datetime.now(roma_tz)

    def is_partita_scaduta(partita):
        dop = row_get(partita, 'data_ora_partita')
        if not dop:
            return False
        orario_naive = parse_flexible_datetime(str(dop))
        if not orario_naive:
            return False
        return ora_corrente > pytz.utc.localize(orario_naive).astimezone(roma_tz)

    with db_conn() as conn:
        user = utente_corrente(conn)
        if not user:
            return redirect(url_for('logout'))
        user_id = row_get(user, 'id')
        partite = db_fetchall(conn,
            "SELECT * FROM partite WHERE giornata = ? AND pronosticabile = TRUE", (giornata,))

        giocatori_per_partita = {}
        for partita in partite:
            sc = (row_get(partita, 'squadra_casa') or '').upper()
            so = (row_get(partita, 'squadra_ospite') or '').upper()
            giocatori_per_partita[row_get(partita, 'id')] = db_fetchall(conn,
                "SELECT nome_giocatore, squadra FROM giocatori WHERE squadra = ? OR squadra = ? "
                "ORDER BY squadra, nome_giocatore", (sc, so))

        pronostici_salvati = db_fetchall(conn,
            "SELECT * FROM pronostici_giornata WHERE id_utente = ? AND id_partita IN "
            "(SELECT id FROM partite WHERE giornata = ?)", (user_id, giornata))
        pronostici_dict = {row_get(p, 'id_partita'): p for p in pronostici_salvati}

        if request.method == "POST":
            for partita in partite:
                if is_partita_scaduta(partita):
                    continue
                pid = row_get(partita, 'id')
                esito = request.form.get(f"esito_{pid}")
                r_casa = _safe_int(request.form.get(f"risultato_casa_{pid}"), lo=0, hi=20)
                r_osp = _safe_int(request.form.get(f"risultato_ospite_{pid}"), lo=0, hi=20)
                marcatore = (request.form.get(f"marcatore_{pid}") or "").strip()
                if esito or (r_casa is not None and r_osp is not None) or marcatore:
                    if pid in pronostici_dict:
                        db_execute(conn,
                            "UPDATE pronostici_giornata SET esito_pronosticato=?, "
                            "risultato_casa_pronosticato=?, risultato_ospite_pronosticato=?, "
                            "marcatore_pronosticato=? WHERE id_utente=? AND id_partita=?",
                            (esito, r_casa, r_osp, marcatore, user_id, pid))
                    else:
                        db_execute(conn,
                            "INSERT INTO pronostici_giornata (id_utente, id_partita, "
                            "esito_pronosticato, risultato_casa_pronosticato, "
                            "risultato_ospite_pronosticato, marcatore_pronosticato) "
                            "VALUES (?,?,?,?,?,?)",
                            (user_id, pid, esito, r_casa, r_osp, marcatore))
            db_commit(conn)
            return redirect(url_for("home"))

        scadenze_dict = {}
        pronostici_altri_utenti = {}
        for partita in partite:
            pid = row_get(partita, 'id')
            scaduto = is_partita_scaduta(partita)
            scadenze_dict[pid] = scaduto
            if scaduto:
                pronostici_altri_utenti[pid] = db_fetchall(conn,
                    "SELECT u.nome_utente, pg.* FROM pronostici_giornata pg "
                    "JOIN utenti u ON pg.id_utente = u.id WHERE pg.id_partita = ?", (pid,))

    return render_template("pronostici_giornata.html", partite=partite, giornata=giornata,
                           pronostici_per_partita=pronostici_dict,
                           scadenze=scadenze_dict,
                           pronostici_altri_utenti=pronostici_altri_utenti,
                           giocatori_per_partita=giocatori_per_partita,
                           session=session)


@app.route("/profilo", methods=["GET", "POST"])
def profilo():
    if 'nome_utente' not in session:
        return redirect(url_for('login'))
    with db_conn() as conn:
        user = utente_corrente(conn)
        if not user:
            return redirect(url_for('logout'))
        email_attuale = row_get(user, 'email') or ''
        if request.method == "POST":
            azione = request.form.get('azione')
            if azione == 'email':
                nuova_email = (request.form.get('nuova_email') or '').strip().lower()
                if nuova_email and not EMAIL_RE.match(nuova_email):
                    return render_template('profilo.html', email=email_attuale, session=session,
                                           errore="Formato email non valido.")
                db_execute(conn, "UPDATE utenti SET email = ? WHERE nome_utente = ?",
                           (nuova_email, session['nome_utente']))
                db_commit(conn)
                flash("Email aggiornata.", "success")
                return redirect(url_for('profilo'))
            elif azione == 'password':
                nuova_pw = request.form.get('nuova_password') or ''
                conferma = request.form.get('conferma_password') or ''
                if nuova_pw != conferma:
                    return render_template('profilo.html', email=email_attuale, session=session,
                                           errore="Le password non coincidono.")
                if len(nuova_pw) < MIN_PASSWORD_LEN:
                    return render_template('profilo.html', email=email_attuale, session=session,
                                           errore=f"La password deve avere almeno {MIN_PASSWORD_LEN} caratteri.")
                db_execute(conn,
                           "UPDATE utenti SET password = ?, is_temp_password = FALSE WHERE nome_utente = ?",
                           (hash_password(nuova_pw), session['nome_utente']))
                db_commit(conn)
                flash("Password aggiornata.", "success")
                return redirect(url_for('profilo'))
    return render_template('profilo.html', email=email_attuale, session=session)


@app.route("/api/profilo-info")
def api_profilo_info():
    if 'nome_utente' not in session:
        return {"email": ""}, 401
    with db_conn() as conn:
        user = db_fetchone(conn, "SELECT email FROM utenti WHERE nome_utente = ?",
                           (session['nome_utente'],))
    return {"email": row_get(user, 'email') or ""}


# ═════════════════════════════════════════════════════════════
# ROUTE ADMIN
# ═════════════════════════════════════════════════════════════

@app.route("/admin")
def admin_home():
    if require_admin():
        return "Accesso negato.", 403
    with db_conn() as conn:
        ga_row = db_fetchone(conn, "SELECT giornata FROM stato_giornata WHERE is_attiva = TRUE")
        giornata_attiva = row_get(ga_row, 'giornata') if ga_row else None
        partite_attive = []
        if giornata_attiva:
            partite_attive = db_fetchall(conn,
                "SELECT * FROM partite WHERE giornata = ? AND pronosticabile = TRUE",
                (giornata_attiva,))
    return render_template("admin.html", giornata_attiva=giornata_attiva,
                           partite_attive=partite_attive, session=session)


@app.route("/admin/utenti")
def admin_utenti():
    if require_admin():
        return "Accesso negato.", 403
    with db_conn() as conn:
        utenti = db_fetchall(conn,
            "SELECT id, nome_utente, is_temp_password, is_admin FROM utenti ORDER BY nome_utente")
    return render_template("admin_utenti.html", utenti=utenti, session=session)


@app.route("/admin/resetta-password/<int:id_utente>", methods=["POST"])
def admin_resetta_password(id_utente):
    if require_admin():
        return "Accesso negato.", 403
    pw_temp = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    with db_conn() as conn:
        db_execute(conn,
                   "UPDATE utenti SET password = ?, is_temp_password = TRUE WHERE id = ?",
                   (hash_password(pw_temp), id_utente))
        utente = db_fetchone(conn, "SELECT nome_utente FROM utenti WHERE id = ?", (id_utente,))
        db_commit(conn)
    nome = row_get(utente, 'nome_utente') if utente else 'Utente'
    flash(f"Password temporanea per {nome}: {pw_temp}", "success")
    return redirect(url_for('admin_utenti'))


@app.route("/admin/elimina-utente/<int:id_utente>", methods=["POST"])
def admin_elimina_utente(id_utente):
    if require_admin():
        return "Accesso negato.", 403
    with db_conn() as conn:
        utente = db_fetchone(conn, "SELECT nome_utente, is_admin FROM utenti WHERE id = ?", (id_utente,))
        if not utente:
            flash("Utente non trovato.", "warning")
            return redirect(url_for('admin_utenti'))
        if row_get(utente, 'is_admin'):
            flash("Non puoi eliminare un admin.", "warning")
            return redirect(url_for('admin_utenti'))
        db_execute(conn, "DELETE FROM punteggi WHERE id_utente = ?", (id_utente,))
        db_execute(conn, "DELETE FROM punteggi_giornata WHERE id_utente = ?", (id_utente,))
        db_execute(conn, "DELETE FROM pronostici_giornata WHERE id_utente = ?", (id_utente,))
        db_execute(conn, "DELETE FROM pronostici_iniziali WHERE id_utente = ?", (id_utente,))
        db_execute(conn, "DELETE FROM utenti WHERE id = ?", (id_utente,))
        db_commit(conn)
    flash(f"Utente {row_get(utente, 'nome_utente')} eliminato.", "success")
    return redirect(url_for('admin_utenti'))


@app.route("/admin/gestisci-partite")
def admin_gestisci_partite():
    if require_admin():
        return "Accesso negato.", 403
    with db_conn() as conn:
        giornata_sel = request.args.get('giornata', type=int)
        giornate_rows = db_fetchall(conn,
            "SELECT DISTINCT giornata FROM partite ORDER BY giornata")
        giornate_disponibili = [row_get(r, 'giornata') for r in giornate_rows]
        if giornata_sel:
            partite = db_fetchall(conn,
                "SELECT * FROM partite WHERE giornata = ? ORDER BY data_ora_partita",
                (giornata_sel,))
        else:
            partite = db_fetchall(conn,
                "SELECT * FROM partite ORDER BY giornata, data_ora_partita")
        giornata_attiva_row = db_fetchone(conn,
            "SELECT giornata FROM stato_giornata WHERE is_attiva = TRUE")
        partite_attive = []
        giocatori_per_partita = {}
        giornata_attiva_dict = None
        if giornata_attiva_row:
            g = row_get(giornata_attiva_row, 'giornata')
            giornata_attiva_dict = {'giornata': g}
            partite_attive = db_fetchall(conn,
                "SELECT * FROM partite WHERE giornata = ? AND pronosticabile = TRUE", (g,))
            for partita in partite_attive:
                pid = row_get(partita, 'id')
                sc = (row_get(partita, 'squadra_casa') or '').upper()
                so = (row_get(partita, 'squadra_ospite') or '').upper()
                giocatori_per_partita[pid] = db_fetchall(conn,
                    "SELECT nome_giocatore, squadra FROM giocatori "
                    "WHERE UPPER(squadra) = ? OR UPPER(squadra) = ? "
                    "ORDER BY squadra, nome_giocatore", (sc, so))
    return render_template("admin_gestisci_partite.html", tutte_le_partite=partite,
                           giornate_disponibili=giornate_disponibili,
                           giornata_selezionata=giornata_sel,
                           giornata_attiva=giornata_attiva_dict,
                           partite_attive=partite_attive,
                           giocatori_per_partita=giocatori_per_partita,
                           session=session)


@app.route("/admin/aggiungi-partita", methods=["POST"])
def aggiungi_partita():
    if require_admin():
        return "Accesso negato.", 403
    giornata = _safe_int(request.form.get("giornata"), lo=1, hi=50)
    if giornata is None:
        flash("Giornata non valida.", "warning")
        return redirect(url_for("admin_gestisci_partite"))
    with db_conn() as conn:
        db_execute(conn,
            "INSERT INTO partite (giornata, squadra_casa, squadra_ospite, pronosticabile, data_ora_partita) "
            "VALUES (?,?,?,?,?)",
            (giornata,
             (request.form.get("squadra_casa") or "").upper(),
             (request.form.get("squadra_ospite") or "").upper(),
             request.form.get("pronosticabile") == "on",
             request.form.get("data_ora_partita")))
        db_commit(conn)
    return redirect(url_for("admin_gestisci_partite"))


@app.route("/admin/modifica-partita/<int:id_partita>", methods=["POST"])
def admin_modifica_partita(id_partita):
    if require_admin():
        return "Accesso negato.", 403
    giornata = _safe_int(request.form.get("giornata"), lo=1, hi=50)
    with db_conn() as conn:
        db_execute(conn,
            "UPDATE partite SET giornata=?, squadra_casa=?, squadra_ospite=?, "
            "pronosticabile=?, data_ora_partita=? WHERE id=?",
            (giornata,
             (request.form.get("squadra_casa") or "").upper(),
             (request.form.get("squadra_ospite") or "").upper(),
             request.form.get("pronosticabile") == "on",
             request.form.get("data_ora_partita"), id_partita))
        db_commit(conn)
    return redirect(url_for("admin_gestisci_partite",
                            giornata=request.args.get('giornata')))


@app.route("/admin/elimina-partita/<int:id_partita>", methods=["POST"])
def admin_elimina_partita(id_partita):
    if require_admin():
        return "Accesso negato.", 403
    with db_conn() as conn:
        db_execute(conn, "DELETE FROM partite WHERE id = ?", (id_partita,))
        db_commit(conn)
    return redirect(url_for("admin_gestisci_partite",
                            giornata=request.args.get('giornata')))


@app.route("/admin/risultati-giornata/<int:giornata>", methods=["POST"])
def admin_risultati_giornata(giornata):
    if require_admin():
        return "Accesso negato.", 403
    with db_conn() as conn:
        partite = db_fetchall(conn,
            "SELECT * FROM partite WHERE giornata = ? AND pronosticabile = TRUE", (giornata,))
        for partita in partite:
            pid = row_get(partita, 'id')
            r_casa = _safe_int(request.form.get(f"risultato_casa_{pid}", "").strip(), lo=0, hi=20)
            r_osp = _safe_int(request.form.get(f"risultato_ospite_{pid}", "").strip(), lo=0, hi=20)
            marcatori_lista = request.form.getlist(f"marcatore_{pid}[]")
            marcatori_validi = [m.strip() for m in marcatori_lista
                                if m.strip() and m.strip() != 'Nessun marcatore']
            if not marcatori_validi:
                speciali = [m.strip() for m in marcatori_lista
                            if m.strip() in ('Nessun marcatore', 'Autogol')]
                marcatore_finale = speciali[0] if speciali else None
            else:
                marcatore_finale = ', '.join(marcatori_validi)
            db_execute(conn,
                "UPDATE partite SET risultato_casa_reale=?, risultato_ospite_reale=?, "
                "marcatore_reale=? WHERE id=?",
                (r_casa, r_osp, marcatore_finale, pid))
        db_commit(conn)
    flash("Risultati salvati con successo!", "success")
    return redirect(url_for("admin_gestisci_partite"))


@app.route("/admin/importa-risultati/<int:giornata>", methods=["POST"])
def admin_importa_risultati(giornata):
    if require_admin():
        return "Accesso negato.", 403
    try:
        risultati_api, errore = get_risultati_giornata(giornata)
        if errore:
            flash(f"Errore API: {errore}", "danger")
            return redirect(url_for("admin_home"))
        if not risultati_api:
            flash(f"Nessuna partita terminata trovata per la giornata {giornata}.", "warning")
            return redirect(url_for("admin_home"))
        with db_conn() as conn:
            partite_db = db_fetchall(conn, "SELECT * FROM partite WHERE giornata = ?", (giornata,))
            aggiornate = 0
            non_trovate = []
            for partita in partite_db:
                sc = (row_get(partita, 'squadra_casa') or '').upper()
                so = (row_get(partita, 'squadra_ospite') or '').upper()
                match_api = next((r for r in risultati_api
                                  if (sc in r['home'] or r['home'] in sc)
                                  and (so in r['away'] or r['away'] in so)), None)
                if match_api:
                    db_execute(conn,
                        "UPDATE partite SET risultato_casa_reale=?, risultato_ospite_reale=?, "
                        "marcatore_reale=? WHERE id=?",
                        (match_api['gol_home'], match_api['gol_away'],
                         match_api['marcatori_str'], row_get(partita, 'id')))
                    aggiornate += 1
                else:
                    non_trovate.append(f"{sc} vs {so}")
            db_commit(conn)
        msg = f"Risultati importati: {aggiornate} partite aggiornate."
        if non_trovate:
            msg += f" Non trovate: {', '.join(non_trovate)} — aggiorna manualmente."
        flash(msg, "success" if not non_trovate else "warning")
    except Exception as e:
        log.exception("Errore importazione risultati")
        flash(f"Errore durante l'importazione: {str(e)}", "danger")
    return redirect(url_for("admin_home"))


@app.route("/admin/invia-reminder/<int:giornata>", methods=["POST"])
def admin_invia_reminder(giornata):
    if require_admin():
        return "Accesso negato.", 403
    try:
        with db_conn() as conn:
            partite = db_fetchall(conn,
                "SELECT squadra_casa, squadra_ospite, data_ora_partita "
                "FROM partite WHERE giornata = ? AND pronosticabile = TRUE", (giornata,))
            utenti_email = db_fetchall(conn,
                "SELECT email FROM utenti WHERE email IS NOT NULL AND email != ''")
        destinatari = [row_get(u, 'email') for u in utenti_email if row_get(u, 'email')]
        if not destinatari:
            flash("Nessun utente con email registrata.", "warning")
            return redirect(url_for('admin_home'))
        partite_list = [{'squadra_casa': row_get(p, 'squadra_casa'),
                         'squadra_ospite': row_get(p, 'squadra_ospite'),
                         'data_ora_partita': row_get(p, 'data_ora_partita')} for p in partite]
        invia_email_async(destinatari,
                          f"⚽ FantaSerieA — Reminder Giornata {giornata}: inserisci i pronostici!",
                          build_email_giornata(giornata, partite_list))
        flash(f"Reminder in invio a {len(destinatari)} utenti!", "success")
    except Exception as e:
        log.exception("Errore invio reminder")
        flash(f"Errore invio reminder: {str(e)}", "danger")
    return redirect(url_for('admin_home'))


@app.route("/admin/aggiorna-risultati-massivo", methods=["POST"])
def admin_aggiorna_risultati_massivo():
    """Lancia l'aggiornamento massivo in background per evitare timeout gunicorn."""
    if require_admin():
        return "Accesso negato.", 403

    def _esegui_massivo():
        import time
        log.info("[MASSIVO] Avvio aggiornamento storico risultati...")
        try:
            with db_conn() as conn:
                giornate = db_fetchall(conn,
                    "SELECT giornata FROM stato_giornata WHERE is_in_archivio = TRUE ORDER BY giornata")
                aggiornate = 0
                for i, g_row in enumerate(giornate):
                    g = row_get(g_row, 'giornata')
                    if i > 0 and i % 9 == 0:
                        log.info(f"[MASSIVO] Pausa rate limit dopo {i} chiamate...")
                        time.sleep(62)
                    try:
                        risultati_api, errore = get_risultati_giornata(g)
                        if errore or not risultati_api:
                            log.info(f"[MASSIVO] G{g} saltata: {errore or 'nessun risultato'}")
                            continue
                        partite_db = db_fetchall(conn,
                            "SELECT * FROM partite WHERE giornata = ?", (g,))
                        for partita in partite_db:
                            sc = (row_get(partita, 'squadra_casa') or '').upper()
                            so = (row_get(partita, 'squadra_ospite') or '').upper()
                            match_api = next((r for r in risultati_api
                                              if (sc in r['home'] or r['home'] in sc)
                                              and (so in r['away'] or r['away'] in so)), None)
                            if match_api:
                                db_execute(conn,
                                    "UPDATE partite SET risultato_casa_reale=?, risultato_ospite_reale=? WHERE id=?",
                                    (match_api['gol_home'], match_api['gol_away'],
                                     row_get(partita, 'id')))
                        db_commit(conn)
                        aggiornate += 1
                        log.info(f"[MASSIVO] G{g} aggiornata ({aggiornate}/{len(giornate)})")
                        time.sleep(7)
                    except Exception:
                        log.exception(f"[MASSIVO] Errore G{g}")
                log.info(f"[MASSIVO] Completato: {aggiornate}/{len(giornate)} giornate aggiornate.")
        except Exception:
            log.exception("[MASSIVO] Errore generale")

    threading.Thread(target=_esegui_massivo, daemon=True).start()
    flash("Aggiornamento storico avviato in background. Controlla i log per il progresso (~4 minuti).", "info")
    return redirect(url_for('admin_gestisci_partite'))


@app.route("/admin/importa-giornata", methods=["GET", "POST"])
def admin_importa_giornata():
    if require_admin():
        return "Accesso negato.", 403
    partite_da_importare = []
    giornata_selezionata = None
    invio_email = False
    msg_email = ''
    if request.method == "POST":
        giornata_selezionata = _safe_int(request.form.get("giornata"), lo=1, hi=50)
        if giornata_selezionata is None:
            flash("Giornata non valida.", "warning")
            return redirect(url_for('admin_importa_giornata'))
        # Pesco partite dalla API
        try:
            url = f"{FOOTBALL_API_BASE}/competitions/{SERIE_A_CODE}/matches"
            r = http_requests.get(url, headers=api_headers(),
                                  params={'matchday': giornata_selezionata}, timeout=15)
            if r.status_code != 200:
                flash(f"API risposta {r.status_code}", "danger")
                return redirect(url_for('admin_importa_giornata'))
            data = r.json()
            for m in data.get('matches', []):
                partite_da_importare.append({
                    'squadra_casa': (m['homeTeam']['name'] or '').upper(),
                    'squadra_ospite': (m['awayTeam']['name'] or '').upper(),
                    'data_ora': m.get('utcDate', ''),
                })
        except Exception:
            log.exception("Errore lettura calendario API")
            flash("Errore lettura calendario API.", "danger")
            return redirect(url_for('admin_importa_giornata'))

        # Se l'admin ha confermato la selezione, importo
        if request.form.get('conferma') == '1':
            partite_sel_idx = request.form.getlist('seleziona[]')
            partite_sel = []
            for idx in partite_sel_idx:
                try:
                    partite_sel.append(partite_da_importare[int(idx)])
                except (ValueError, IndexError):
                    continue
            if partite_sel:
                with db_conn() as conn:
                    for p in partite_sel:
                        db_execute(conn,
                            "INSERT INTO partite (giornata, squadra_casa, squadra_ospite, pronosticabile, data_ora_partita) "
                            "VALUES (?, ?, ?, TRUE, ?)",
                            (giornata_selezionata, p['squadra_casa'], p['squadra_ospite'], p['data_ora']))
                    if USE_POSTGRES:
                        db_execute(conn,
                            "INSERT INTO stato_giornata (giornata, is_attiva) VALUES (?, TRUE) "
                            "ON CONFLICT (giornata) DO UPDATE SET is_attiva = TRUE",
                            (giornata_selezionata,))
                        db_execute(conn,
                            "UPDATE stato_giornata SET is_attiva = FALSE WHERE giornata != ?",
                            (giornata_selezionata,))
                    else:
                        db_execute(conn,
                            "INSERT OR IGNORE INTO stato_giornata (giornata, is_attiva) VALUES (?, 1)",
                            (giornata_selezionata,))
                        db_execute(conn,
                            "UPDATE stato_giornata SET is_attiva = 1 WHERE giornata = ?",
                            (giornata_selezionata,))
                        db_execute(conn,
                            "UPDATE stato_giornata SET is_attiva = 0 WHERE giornata != ?",
                            (giornata_selezionata,))
                    db_commit(conn)

                # Email opzionale
                if request.form.get('invia_email') == 'on':
                    with db_conn() as conn:
                        utenti_email = db_fetchall(conn,
                            "SELECT email FROM utenti WHERE email IS NOT NULL AND email != ''")
                    destinatari = [row_get(u, 'email') for u in utenti_email if row_get(u, 'email')]
                    if destinatari:
                        invia_email_async(destinatari,
                            f"⚽ FantaSerieA — Giornata {giornata_selezionata} disponibile!",
                            build_email_giornata(giornata_selezionata, partite_sel))
                        msg_email = f" Email inviate a {len(destinatari)} utenti."

                session['flash_message'] = (
                    f"Giornata {giornata_selezionata}: {len(partite_sel)} partite importate.{msg_email}")
                return redirect(url_for('admin_home'))

    return render_template('admin_importa_giornata.html',
                           partite=partite_da_importare,
                           giornata_selezionata=giornata_selezionata,
                           session=session)


@app.route("/admin/email-utenti", methods=["GET", "POST"])
def admin_email_utenti():
    if require_admin():
        return "Accesso negato.", 403
    with db_conn() as conn:
        if request.method == "POST":
            utenti = db_fetchall(conn, "SELECT id FROM utenti")
            for utente in utenti:
                uid = row_get(utente, 'id')
                email = (request.form.get(f"email_{uid}", "") or "").strip()
                if email:
                    if not EMAIL_RE.match(email):
                        continue
                    db_execute(conn, "UPDATE utenti SET email = ? WHERE id = ?", (email, uid))
            db_commit(conn)
            flash("Email utenti aggiornate con successo!", "success")
            return redirect(url_for('admin_email_utenti'))
        utenti = db_fetchall(conn,
            "SELECT id, nome_utente, email FROM utenti ORDER BY nome_utente")
    return render_template("admin_email_utenti.html", utenti=utenti, session=session)


@app.route("/admin/gestisci-email", methods=["GET", "POST"])
def admin_gestisci_email():
    if require_admin():
        return "Accesso negato.", 403
    with db_conn() as conn:
        if request.method == "POST":
            utenti = db_fetchall(conn, "SELECT id FROM utenti")
            aggiornati = 0
            for utente in utenti:
                uid = row_get(utente, 'id')
                email = (request.form.get(f"email_{uid}", "") or "").strip().lower()
                if email and EMAIL_RE.match(email):
                    db_execute(conn, "UPDATE utenti SET email = ? WHERE id = ?", (email, uid))
                    aggiornati += 1
            db_commit(conn)
            flash(f"Email aggiornate per {aggiornati} utenti.", "success")
            return redirect(url_for('admin_gestisci_email'))
        utenti = db_fetchall(conn,
            "SELECT id, nome_utente, email FROM utenti ORDER BY nome_utente")
    return render_template('admin_gestisci_email.html', utenti=utenti, session=session)


@app.route("/admin/archivia-giornata/<int:giornata>", methods=["POST"])
def archivia_giornata(giornata):
    if require_admin():
        return "Accesso negato.", 403
    with db_conn() as conn:
        db_execute(conn,
            "UPDATE stato_giornata SET is_attiva = FALSE, is_in_archivio = TRUE WHERE giornata = ?",
            (giornata,))
        prossima = giornata + 1
        if USE_POSTGRES:
            db_execute(conn,
                "INSERT INTO stato_giornata (giornata, is_attiva) VALUES (?, TRUE) "
                "ON CONFLICT (giornata) DO UPDATE SET is_attiva = TRUE", (prossima,))
        else:
            db_execute(conn,
                "INSERT OR IGNORE INTO stato_giornata (giornata, is_attiva) VALUES (?, 1)", (prossima,))
            db_execute(conn,
                "UPDATE stato_giornata SET is_attiva = 1 WHERE giornata = ?", (prossima,))
        db_commit(conn)
    return redirect(url_for("admin_home"))


@app.route("/admin/calcola-punti-giornata/<int:giornata>", methods=["POST"])
def admin_calcola_punti_giornata(giornata):
    if require_admin():
        return "Accesso negato.", 403
    flash(calcola_e_aggiorna_punti_giornata(giornata), "success")
    return redirect(url_for("admin_home"))


@app.route("/calcola-punteggi", methods=["POST"])
def calcola_punteggi():
    if require_admin():
        return "Accesso negato.", 403
    flash(ricalcola_punteggi_totali(), "success")
    return redirect(url_for("admin_home"))


@app.route("/admin/gestisci-pronostici/<int:giornata>", methods=["GET", "POST"])
def admin_gestisci_pronostici(giornata):
    if require_admin():
        return "Accesso negato.", 403
    with db_conn() as conn:
        if request.method == "POST":
            action = request.form.get('action')
            if action == 'modifica':
                pid = request.form.get('id_pronostico')
                db_execute(conn,
                    "UPDATE pronostici_giornata SET esito_pronosticato=?, "
                    "risultato_casa_pronosticato=?, risultato_ospite_pronosticato=?, "
                    "marcatore_pronosticato=? WHERE id=?",
                    (request.form.get('esito'),
                     _safe_int(request.form.get('risultato_casa'), lo=0, hi=20),
                     _safe_int(request.form.get('risultato_ospite'), lo=0, hi=20),
                     request.form.get('marcatore'), pid))
                db_commit(conn)
                return redirect(url_for('admin_gestisci_pronostici', giornata=giornata))
            elif action == 'cancella':
                pid = request.form.get('id_pronostico')
                db_execute(conn, "DELETE FROM pronostici_giornata WHERE id = ?", (pid,))
                db_commit(conn)
                return redirect(url_for('admin_gestisci_pronostici', giornata=giornata))
        partite = db_fetchall(conn,
            "SELECT * FROM partite WHERE giornata = ? AND pronosticabile = TRUE", (giornata,))
        pronostici_per_partita = {}
        for partita in partite:
            pid = row_get(partita, 'id')
            pronostici_per_partita[pid] = db_fetchall(conn,
                "SELECT u.nome_utente, pg.* FROM pronostici_giornata pg "
                "JOIN utenti u ON pg.id_utente = u.id WHERE pg.id_partita = ?", (pid,))
    return render_template('admin_gestisci_pronostici.html', giornata=giornata,
                           partite=partite,
                           pronostici_per_partita=pronostici_per_partita,
                           session=session)


@app.route("/admin/gestisci-pronostici-iniziali")
def admin_gestisci_pronostici_iniziali():
    if require_admin():
        return "Accesso negato.", 403
    with db_conn() as conn:
        pronostici = db_fetchall(conn,
            "SELECT u.nome_utente, pi.* FROM utenti u "
            "JOIN pronostici_iniziali pi ON u.id = pi.id_utente")
        lock_row = db_fetchone(conn, "SELECT is_locked FROM stato_pronostici_iniziali WHERE id = 1")
        is_locked = row_get(lock_row, 'is_locked') if lock_row else False
    return render_template("admin_gestisci_pronostici_iniziali.html",
                           pronostici=pronostici, is_locked=is_locked, session=session)


@app.route("/admin/elimina-pronostico-iniziale/<int:id_pronostico>", methods=["POST"])
def admin_elimina_pronostico_iniziale(id_pronostico):
    if require_admin():
        return "Accesso negato.", 403
    with db_conn() as conn:
        db_execute(conn, "DELETE FROM pronostici_iniziali WHERE id = ?", (id_pronostico,))
        db_commit(conn)
    return redirect(url_for('admin_gestisci_pronostici_iniziali'))


@app.route("/admin/gestisci-finalizzazione")
def admin_gestisci_finalizzazione():
    if require_admin():
        return "Accesso negato.", 403
    with db_conn() as conn:
        lock_row = db_fetchone(conn, "SELECT is_locked FROM stato_pronostici_iniziali WHERE id = 1")
        is_locked = row_get(lock_row, 'is_locked') if lock_row else False
    return render_template("admin_finalizzazione.html", is_locked=is_locked, session=session)


@app.route("/admin/blocca-pronostici-iniziali", methods=["POST"])
def blocca_pronostici_iniziali():
    if require_admin():
        return "Accesso negato.", 403
    with db_conn() as conn:
        db_execute(conn, "UPDATE stato_pronostici_iniziali SET is_locked = TRUE WHERE id = 1")
        db_commit(conn)
    return redirect(url_for('admin_gestisci_finalizzazione'))


@app.route("/admin/sblocca-pronostici-iniziali", methods=["POST"])
def sblocca_pronostici_iniziali():
    if require_admin():
        return "Accesso negato.", 403
    with db_conn() as conn:
        db_execute(conn, "UPDATE stato_pronostici_iniziali SET is_locked = FALSE WHERE id = 1")
        db_commit(conn)
    return redirect(url_for('admin_gestisci_finalizzazione'))


@app.route("/admin/calcola-punti-finali", methods=["GET", "POST"])
def admin_calcola_punti_finali():
    if require_admin():
        return "Accesso negato.", 403
    messaggio = None
    with db_conn() as conn:
        if request.method == 'POST':
            db_execute(conn,
                "UPDATE risultati_finali SET squadra_1=?, squadra_2=?, squadra_3=?, "
                "squadra_4=?, capocannoniere=? WHERE id=1",
                (request.form.get('squadra_1'), request.form.get('squadra_2'),
                 request.form.get('squadra_3'), request.form.get('squadra_4'),
                 request.form.get('capocannoniere')))
            db_commit(conn)
            messaggio = ricalcola_punteggi_finali()
        rf = db_fetchone(conn, "SELECT * FROM risultati_finali WHERE id = 1")
    return render_template("admin_calcola_punti_finali.html",
                           risultati_finali=rf, messaggio=messaggio, session=session)


@app.route("/admin/modifica-giornata-archiviata/<int:giornata>", methods=["GET", "POST"])
def admin_modifica_giornata_archiviata(giornata):
    if require_admin():
        return "Accesso negato.", 403
    with db_conn() as conn:
        if request.method == "POST":
            partite = db_fetchall(conn,
                "SELECT * FROM partite WHERE giornata = ? AND pronosticabile = TRUE", (giornata,))
            for partita in partite:
                pid = row_get(partita, 'id')
                r_casa = _safe_int(request.form.get(f"risultato_casa_{pid}", "").strip(), lo=0, hi=20)
                r_osp = _safe_int(request.form.get(f"risultato_ospite_{pid}", "").strip(), lo=0, hi=20)
                marcatore = (request.form.get(f"marcatore_{pid}", "") or "").strip() or None
                db_execute(conn,
                    "UPDATE partite SET risultato_casa_reale=?, risultato_ospite_reale=?, "
                    "marcatore_reale=? WHERE id=?", (r_casa, r_osp, marcatore, pid))
            db_commit(conn)
            flash(f"Risultati giornata {giornata} aggiornati.", "success")
            return redirect(url_for('admin_modifica_giornata_archiviata', giornata=giornata))
        partite = db_fetchall(conn,
            "SELECT * FROM partite WHERE giornata = ? AND pronosticabile = TRUE "
            "ORDER BY data_ora_partita", (giornata,))
        giocatori_per_partita = {}
        for partita in partite:
            pid = row_get(partita, 'id')
            sc = (row_get(partita, 'squadra_casa') or '').upper()
            so = (row_get(partita, 'squadra_ospite') or '').upper()
            giocatori_per_partita[pid] = db_fetchall(conn,
                "SELECT nome_giocatore, squadra FROM giocatori "
                "WHERE UPPER(squadra) = ? OR UPPER(squadra) = ? "
                "ORDER BY squadra, nome_giocatore", (sc, so))
    return render_template("admin_modifica_giornata_archiviata.html",
                           giornata=giornata, partite=partite,
                           giocatori_per_partita=giocatori_per_partita, session=session)


@app.route("/giornata/<int:giornata>/classifica-cumulativa")
def classifica_cumulativa_giornata(giornata):
    """Classifica cumulativa fino alla giornata indicata — somma da punteggi_giornata."""
    if 'nome_utente' not in session:
        return redirect(url_for('login'))
    with db_conn() as conn:
        rows = db_fetchall(conn,
            "SELECT u.nome_utente, COALESCE(SUM(pg.punti), 0) AS punteggio "
            "FROM utenti u LEFT JOIN punteggi_giornata pg "
            "  ON pg.id_utente = u.id AND pg.giornata <= ? "
            "GROUP BY u.id, u.nome_utente ORDER BY punteggio DESC, u.nome_utente",
            (giornata,))
        classifica = [{'nome_utente': row_get(r, 'nome_utente'),
                       'punteggio': row_get(r, 'punteggio') or 0} for r in rows]
    return render_template("classifica_cumulativa.html",
                           giornata=giornata, classifica=classifica, session=session)


@app.route("/admin/ricalcola-tutta-la-classifica", methods=["POST"])
def admin_ricalcola_tutta_la_classifica():
    if require_admin():
        return "Accesso negato.", 403
    flash(ricalcola_punteggi_totali(), "success")
    return redirect(url_for("admin_home"))


# ═════════════════════════════════════════════════════════════
# AVVIO
# ═════════════════════════════════════════════════════════════

# Esegui create_tables all'avvio anche con gunicorn
with app.app_context():
    try:
        create_tables()
    except Exception:
        log.exception("ERRORE create_tables")


if __name__ == "__main__":
    debug_mode = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(host='127.0.0.1', port=5000, debug=debug_mode)
