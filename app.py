"""
FantaSerieA — app factory (V3)

Crea e configura l'applicazione Flask.
Chiamare create_app() per ottenere un'istanza configurata.
"""

import logging
import os
from datetime import timedelta

import pytz
from flask import Flask, session, g as flask_g, render_template
from flask_wtf.csrf import generate_csrf
from flask_talisman import Talisman

from config import get_config
from extensions import csrf, limiter, db
from db_utils import db_conn, db_execute, db_fetchone, db_fetchall, db_commit, row_get, USE_POSTGRES
from services.game_logic import parse_flexible_datetime, pulisci_username


# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=os.environ.get('LOG_LEVEL', 'INFO'),
    format='[%(asctime)s] %(levelname)s %(name)s — %(message)s',
)
log = logging.getLogger('fanta')


# ─── CSP (Content Security Policy) ───────────────────────────────────────────
# Calibrata sul progetto reale:
# - style-src 'self':         CSS in static/css/app.css + Google Fonts
# - script-src 'self' 'unsafe-inline': piccoli script inline in base.html
#   (menu avatar, tab-bar highlight). Rimuovere 'unsafe-inline' quando
#   questi script verranno spostati in file .js statici.
# - img-src 'self' data::     possibili favicon inline base64
# - font-src:                 Google Fonts

CSP = {
    'default-src':  "'self'",
    # Separazione CSP Level 3 per stili:
    # - style-src-elem: solo CSS da file (no <style> inline injection)
    # - style-src-attr: permette style="" attributi (necessari nei template)
    # - style-src: fallback per browser vecchi (stesso di attr)
    'style-src':      ["'self'", "'unsafe-inline'",
                       'https://fonts.googleapis.com',
                       'https://fonts.gstatic.com'],
    'style-src-elem': ["'self'",
                       'https://fonts.googleapis.com',
                       'https://fonts.gstatic.com'],
    'style-src-attr': ["'unsafe-inline'"],
    'font-src':     ["'self'",
                     'https://fonts.gstatic.com'],
    'script-src':   ["'self'", "'unsafe-inline'"],
    'img-src':      ["'self'", 'data:'],
    'connect-src':  "'self'",
    'frame-ancestors': "'none'",
    'base-uri':     "'self'",
    'form-action':  "'self'",
}


# ─── Schema DB ────────────────────────────────────────────────────────────────

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
    db_execute(conn, """CREATE TABLE IF NOT EXISTS punteggi_giornata (
        id SERIAL PRIMARY KEY,
        id_utente INTEGER NOT NULL REFERENCES utenti(id),
        giornata INTEGER NOT NULL,
        punti INTEGER NOT NULL DEFAULT 0,
        UNIQUE (id_utente, giornata))""")
    db_execute(conn,
               "INSERT INTO stato_pronostici_iniziali (id, is_locked) "
               "VALUES (1, FALSE) ON CONFLICT (id) DO NOTHING")
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
            db_execute(conn, "ALTER TABLE utenti ADD COLUMN IF NOT EXISTS "
                             "is_admin BOOLEAN NOT NULL DEFAULT FALSE")
        except Exception:
            log.exception('Errore migrazione schema (postgres)')
    else:
        cur = conn.execute("PRAGMA table_info(utenti)")
        cols = {r[1] for r in cur.fetchall()}
        if 'email' not in cols:
            try:
                conn.execute("ALTER TABLE utenti ADD COLUMN email TEXT")
            except Exception:
                log.exception('Errore aggiunta colonna email')
        if 'is_admin' not in cols:
            try:
                conn.execute("ALTER TABLE utenti ADD COLUMN "
                             "is_admin BOOLEAN NOT NULL DEFAULT 0")
            except Exception:
                log.exception('Errore aggiunta colonna is_admin')


def _promuovi_admin_storico(conn):
    row = db_fetchone(conn,
                      "SELECT COUNT(*) AS c FROM utenti WHERE is_admin = TRUE"
                      if USE_POSTGRES else
                      "SELECT COUNT(*) AS c FROM utenti WHERE is_admin = 1")
    if (row_get(row, 'c') or 0) == 0:
        legacy = os.environ.get('LEGACY_ADMIN_USERNAME', 'mirko')
        if USE_POSTGRES:
            db_execute(conn,
                       "UPDATE utenti SET is_admin = TRUE WHERE nome_utente = ?",
                       (legacy,))
        else:
            db_execute(conn,
                       "UPDATE utenti SET is_admin = 1 WHERE nome_utente = ?",
                       (legacy,))
        log.info(f"Migrazione: promosso '{legacy}' ad admin.")


def _create_indexes(conn):
    """Indici idempotenti su foreign key e colonne di filtro.
    CREATE INDEX IF NOT EXISTS e' supportato sia da SQLite che da PostgreSQL."""
    idx = (
        'CREATE INDEX IF NOT EXISTS ix_partite_giornata ON partite (giornata)',
        'CREATE INDEX IF NOT EXISTS ix_pg_id_utente ON pronostici_giornata (id_utente)',
        'CREATE INDEX IF NOT EXISTS ix_pg_id_partita ON pronostici_giornata (id_partita)',
        'CREATE INDEX IF NOT EXISTS ix_punteggi_giornata_utente ON punteggi_giornata (id_utente)',
        'CREATE INDEX IF NOT EXISTS ix_giocatori_squadra ON giocatori (squadra)',
        'CREATE INDEX IF NOT EXISTS ix_pi_id_utente ON pronostici_iniziali (id_utente)',
    )
    for stmt in idx:
        try:
            db_execute(conn, stmt)
        except Exception:
            log.exception('Errore creazione indice')


def _pulisci_username_spazi(conn):
    """Migrazione una-tantum: rimuove gli spazi dagli username esistenti.
    In caso di collisione (due nomi che, ripuliti, coinciderebbero) lascia
    invariato il secondo e registra un warning."""
    try:
        rows = db_fetchall(conn, 'SELECT id, nome_utente FROM utenti')
    except Exception:
        log.exception('Errore lettura utenti per pulizia username')
        return
    presenti = {row_get(r, 'nome_utente'): row_get(r, 'id') for r in rows}
    for r in rows:
        orig = row_get(r, 'nome_utente') or ''
        pulito = pulisci_username(orig)
        if pulito == orig or not pulito:
            continue
        altro = presenti.get(pulito)
        if altro is not None and altro != row_get(r, 'id'):
            log.warning(
                f"Username '{orig}' non normalizzato: collide con '{pulito}'.")
            continue
        try:
            db_execute(conn, 'UPDATE utenti SET nome_utente = ? WHERE id = ?',
                       (pulito, row_get(r, 'id')))
            presenti[pulito] = row_get(r, 'id')
            log.info(f"Username normalizzato: '{orig}' -> '{pulito}'")
        except Exception:
            log.exception('Errore normalizzazione username')


def create_tables():
    with db_conn() as conn:
        if USE_POSTGRES:
            _create_tables_postgres(conn)
        else:
            _create_tables_sqlite(conn)
        _migrate_schema(conn)
        _promuovi_admin_storico(conn)
        _pulisci_username_spazi(conn)
        _create_indexes(conn)
        db_commit(conn)


# ─── Filtri Jinja ─────────────────────────────────────────────────────────────

def _register_filters(app: Flask):
    @app.template_filter('datetime_local_italia')
    def datetime_local_italia(s):
        if not s:
            return ''
        try:
            orario_naive = parse_flexible_datetime(str(s))
            if not orario_naive:
                return str(s)
            roma_tz = pytz.timezone('Europe/Rome')
            return (pytz.utc.localize(orario_naive)
                    .astimezone(roma_tz)
                    .strftime('%Y-%m-%dT%H:%M'))
        except Exception:
            return str(s)

    @app.template_filter('fuso_orario_italia')
    def fuso_orario_italia(s):
        if not s:
            return ''
        try:
            orario_naive = parse_flexible_datetime(str(s))
            if not orario_naive:
                return str(s)
            roma_tz = pytz.timezone('Europe/Rome')
            return (pytz.utc.localize(orario_naive)
                    .astimezone(roma_tz)
                    .strftime('%d/%m/%Y %H:%M'))
        except Exception:
            return str(s)


# ─── Factory ──────────────────────────────────────────────────────────────────

def create_app(config=None) -> Flask:
    app = Flask(__name__)

    # Configurazione
    cfg = config or get_config()
    app.secret_key = cfg.SECRET_KEY
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(
        days=cfg.PERMANENT_SESSION_LIFETIME_DAYS)
    app.config['SESSION_COOKIE_HTTPONLY'] = cfg.SESSION_COOKIE_HTTPONLY
    app.config['SESSION_COOKIE_SAMESITE'] = cfg.SESSION_COOKIE_SAMESITE
    app.config['SESSION_COOKIE_SECURE']   = getattr(cfg, 'SESSION_COOKIE_SECURE', False)
    app.config['WTF_CSRF_TIME_LIMIT']     = cfg.WTF_CSRF_TIME_LIMIT
    app.config['WTF_CSRF_ENABLED']        = getattr(cfg, 'WTF_CSRF_ENABLED', True)
    app.config['RATELIMIT_STORAGE_URI']   = cfg.RATELIMIT_STORAGE_URI

    # Config esposta ai blueprint
    app.config['FOOTBALL_API_KEY']  = cfg.FOOTBALL_API_KEY
    app.config['FOOTBALL_API_BASE'] = cfg.FOOTBALL_API_BASE
    app.config['SERIE_A_CODE']      = cfg.SERIE_A_CODE
    app.config['RESEND_API_KEY']    = cfg.RESEND_API_KEY
    app.config['EMAIL_FROM_NAME']   = cfg.EMAIL_FROM_NAME
    app.config['EMAIL_FROM_ADDRESS'] = cfg.EMAIL_FROM_ADDRESS
    app.config['APP_URL']           = cfg.APP_URL

    # SQLAlchemy — usato per i modelli / Alembic
    db_url = getattr(cfg, 'DATABASE_URL', '')
    if db_url:
        if db_url.startswith('postgres://'):
            db_url = db_url.replace('postgres://', 'postgresql://', 1)
        app.config['SQLALCHEMY_DATABASE_URI'] = db_url
    else:
        db_path = os.path.join(app.root_path, 'database.db')
        app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Estensioni
    csrf.init_app(app)
    limiter.init_app(app)
    db.init_app(app)

    # Flask-Talisman (solo in produzione o se forzato)
    talisman_enabled = getattr(cfg, 'TALISMAN_ENABLED', False)
    if talisman_enabled:
        Talisman(
            app,
            content_security_policy=CSP,
            # NB: nessun nonce su script-src. Se si aggiunge un nonce, i browser
            # ignorano 'unsafe-inline' e bloccano gli <script> inline e gli
            # handler onclick usati nei template (es. il menu utente/drawer).
            force_https=True,
            strict_transport_security=True,
            strict_transport_security_max_age=31536000,
            strict_transport_security_include_subdomains=True,
            frame_options='DENY',
            referrer_policy='strict-origin-when-cross-origin',
        )
    else:
        # In sviluppo: solo X-Frame-Options manuale
        @app.after_request
        def _add_minimal_headers(response):
            response.headers.setdefault('X-Frame-Options', 'DENY')
            response.headers.setdefault('X-Content-Type-Options', 'nosniff')
            return response

    # Filtri Jinja
    _register_filters(app)

    # Context processor globale
    @app.context_processor
    def inject_globals():
        # Cache per-richiesta: evita una connessione DB ad ogni render
        if hasattr(flask_g, '_giornata_attiva_cache'):
            g_attiva = flask_g._giornata_attiva_cache
        else:
            g_attiva = None
            try:
                with db_conn() as conn:
                    row = db_fetchone(
                        conn,
                        'SELECT giornata FROM stato_giornata WHERE is_attiva = TRUE',
                    )
                    g_attiva = row_get(row, 'giornata') if row else None
            except Exception:
                log.exception('Errore lettura giornata attiva')
            flask_g._giornata_attiva_cache = g_attiva
        return {
            'giornata_attiva': g_attiva,
            'csrf_token':      generate_csrf,
            'is_admin':        bool(session.get('is_admin')),
        }

    # Error handler (pagine 404/500 coerenti col design)
    @app.errorhandler(404)
    def _not_found(e):
        return render_template('404.html'), 404

    @app.errorhandler(500)
    def _server_error(e):
        return render_template('500.html'), 500

    # Blueprint
    from blueprints.auth  import auth_bp
    from blueprints.gioco import gioco_bp
    from blueprints.admin import admin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(gioco_bp)
    app.register_blueprint(admin_bp)

    # Schema DB al primo avvio
    with app.app_context():
        try:
            create_tables()
        except Exception:
            log.exception('ERRORE create_tables')

    return app


# ─── Entry point (locale / gunicorn) ─────────────────────────────────────────

app = create_app()   # usato da gunicorn: gunicorn app:app

if __name__ == '__main__':
    debug_mode = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(host='127.0.0.1', port=5000, debug=debug_mode)
