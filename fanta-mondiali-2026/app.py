"""
Fanta Mondiali 2026 — app factory
"""

import logging, os
from datetime import timedelta

import pytz
from flask import Flask, session
from flask_wtf.csrf import generate_csrf
from flask_talisman import Talisman
from apscheduler.schedulers.background import BackgroundScheduler

from config import get_config
from extensions import csrf, limiter, db
from db_utils import db_conn, db_execute, db_fetchone, db_fetchall, db_commit, row_get, USE_POSTGRES
from services.game_logic import parse_flexible_datetime

logging.basicConfig(
    level=os.environ.get('LOG_LEVEL', 'INFO'),
    format='[%(asctime)s] %(levelname)s %(name)s — %(message)s',
)
log = logging.getLogger('mondiali')

CSP = {
    'default-src':    "'self'",
    'style-src':      ["'self'", "'unsafe-inline'",
                       'https://fonts.googleapis.com', 'https://fonts.gstatic.com'],
    'style-src-elem': ["'self'", 'https://fonts.googleapis.com', 'https://fonts.gstatic.com'],
    'style-src-attr': ["'unsafe-inline'"],
    'font-src':       ["'self'", 'https://fonts.gstatic.com'],
    'script-src':     ["'self'", "'unsafe-inline'"],
    'img-src':        ["'self'", 'data:'],
    'connect-src':    "'self'",
    'frame-ancestors': "'none'",
    'base-uri':       "'self'",
    'form-action':    "'self'",
}


# ─── Schema DB ────────────────────────────────────────────────────────────────

def _create_tables_postgres(conn):
    sqls = [
        """CREATE TABLE IF NOT EXISTS utenti (
            id SERIAL PRIMARY KEY, nome_utente TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL, is_temp_password BOOLEAN NOT NULL DEFAULT FALSE,
            is_admin BOOLEAN NOT NULL DEFAULT FALSE, email TEXT)""",
        """CREATE TABLE IF NOT EXISTS partite (
            id SERIAL PRIMARY KEY,
            giornata INTEGER,
            fase TEXT NOT NULL DEFAULT 'gironi',
            girone TEXT,
            squadra_casa TEXT NOT NULL, squadra_ospite TEXT NOT NULL,
            risultato_casa_reale INTEGER, risultato_ospite_reale INTEGER,
            gol_casa_90 INTEGER, gol_ospite_90 INTEGER,
            vincitore TEXT,
            marcatore_reale TEXT,
            pronosticabile BOOLEAN NOT NULL DEFAULT FALSE,
            data_ora_partita TEXT)""",
        """CREATE TABLE IF NOT EXISTS pronostici_giornata (
            id SERIAL PRIMARY KEY, id_utente INTEGER NOT NULL REFERENCES utenti(id),
            id_partita INTEGER NOT NULL REFERENCES partite(id),
            esito_pronosticato TEXT, risultato_casa_pronosticato INTEGER,
            risultato_ospite_pronosticato INTEGER, marcatore_pronosticato TEXT)""",
        """CREATE TABLE IF NOT EXISTS pronostici_eliminazione (
            id SERIAL PRIMARY KEY,
            id_utente INTEGER NOT NULL REFERENCES utenti(id),
            id_partita INTEGER NOT NULL REFERENCES partite(id),
            vincitore TEXT, gol_casa_90 INTEGER, gol_ospite_90 INTEGER,
            UNIQUE(id_utente, id_partita))""",
        """CREATE TABLE IF NOT EXISTS pronostici_torneo (
            id SERIAL PRIMARY KEY,
            id_utente INTEGER NOT NULL UNIQUE REFERENCES utenti(id),
            vincitore TEXT, finalista TEXT,
            semifinalista_1 TEXT, semifinalista_2 TEXT, capocannoniere TEXT)""",
        """CREATE TABLE IF NOT EXISTS punteggi (
            id SERIAL PRIMARY KEY, id_utente INTEGER NOT NULL UNIQUE REFERENCES utenti(id),
            punteggio_totale INTEGER NOT NULL DEFAULT 0)""",
        """CREATE TABLE IF NOT EXISTS punteggi_giornata (
            id SERIAL PRIMARY KEY, id_utente INTEGER NOT NULL REFERENCES utenti(id),
            giornata INTEGER NOT NULL, punti INTEGER NOT NULL DEFAULT 0,
            UNIQUE(id_utente, giornata))""",
        """CREATE TABLE IF NOT EXISTS punteggi_fase (
            id SERIAL PRIMARY KEY, id_utente INTEGER NOT NULL REFERENCES utenti(id),
            fase TEXT NOT NULL, punti INTEGER NOT NULL DEFAULT 0,
            UNIQUE(id_utente, fase))""",
        """CREATE TABLE IF NOT EXISTS stato_giornata (
            id SERIAL PRIMARY KEY, giornata INTEGER NOT NULL UNIQUE,
            is_attiva BOOLEAN NOT NULL DEFAULT FALSE,
            is_in_archivio BOOLEAN NOT NULL DEFAULT FALSE)""",
        """CREATE TABLE IF NOT EXISTS stato_fase (
            id SERIAL PRIMARY KEY, fase TEXT NOT NULL UNIQUE,
            is_attiva BOOLEAN NOT NULL DEFAULT FALSE,
            is_in_archivio BOOLEAN NOT NULL DEFAULT FALSE,
            pronostici_locked BOOLEAN NOT NULL DEFAULT FALSE)""",
        """CREATE TABLE IF NOT EXISTS stato_pronostici_torneo (
            id INTEGER PRIMARY KEY, is_locked BOOLEAN NOT NULL DEFAULT FALSE)""",
        """CREATE TABLE IF NOT EXISTS risultati_torneo (
            id INTEGER PRIMARY KEY, vincitore TEXT, finalista TEXT,
            semifinalista_1 TEXT, semifinalista_2 TEXT, capocannoniere TEXT)""",
        """CREATE TABLE IF NOT EXISTS giocatori (
            id SERIAL PRIMARY KEY, nome_giocatore TEXT NOT NULL, squadra TEXT NOT NULL)""",
    ]
    for sql in sqls:
        db_execute(conn, sql)
    db_execute(conn, "INSERT INTO stato_pronostici_torneo (id,is_locked) VALUES (1,FALSE) ON CONFLICT(id) DO NOTHING")
    db_execute(conn, "INSERT INTO risultati_torneo (id) VALUES (1) ON CONFLICT(id) DO NOTHING")


def _create_tables_sqlite(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS utenti (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nome_utente TEXT NOT NULL UNIQUE,
        password TEXT NOT NULL, is_temp_password BOOLEAN NOT NULL DEFAULT 0,
        is_admin BOOLEAN NOT NULL DEFAULT 0, email TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS partite (
        id INTEGER PRIMARY KEY AUTOINCREMENT, giornata INTEGER,
        fase TEXT NOT NULL DEFAULT 'gironi', girone TEXT,
        squadra_casa TEXT NOT NULL, squadra_ospite TEXT NOT NULL,
        risultato_casa_reale INTEGER, risultato_ospite_reale INTEGER,
        gol_casa_90 INTEGER, gol_ospite_90 INTEGER, vincitore TEXT,
        marcatore_reale TEXT, pronosticabile BOOLEAN NOT NULL DEFAULT 0,
        data_ora_partita TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS pronostici_giornata (
        id INTEGER PRIMARY KEY AUTOINCREMENT, id_utente INTEGER NOT NULL,
        id_partita INTEGER NOT NULL, esito_pronosticato TEXT,
        risultato_casa_pronosticato INTEGER, risultato_ospite_pronosticato INTEGER,
        marcatore_pronosticato TEXT,
        FOREIGN KEY(id_utente) REFERENCES utenti(id),
        FOREIGN KEY(id_partita) REFERENCES partite(id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS pronostici_eliminazione (
        id INTEGER PRIMARY KEY AUTOINCREMENT, id_utente INTEGER NOT NULL,
        id_partita INTEGER NOT NULL, vincitore TEXT, gol_casa_90 INTEGER,
        gol_ospite_90 INTEGER, UNIQUE(id_utente, id_partita),
        FOREIGN KEY(id_utente) REFERENCES utenti(id),
        FOREIGN KEY(id_partita) REFERENCES partite(id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS pronostici_torneo (
        id INTEGER PRIMARY KEY AUTOINCREMENT, id_utente INTEGER NOT NULL UNIQUE,
        vincitore TEXT, finalista TEXT, semifinalista_1 TEXT, semifinalista_2 TEXT,
        capocannoniere TEXT, FOREIGN KEY(id_utente) REFERENCES utenti(id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS punteggi (
        id INTEGER PRIMARY KEY AUTOINCREMENT, id_utente INTEGER NOT NULL UNIQUE,
        punteggio_totale INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY(id_utente) REFERENCES utenti(id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS punteggi_giornata (
        id INTEGER PRIMARY KEY AUTOINCREMENT, id_utente INTEGER NOT NULL,
        giornata INTEGER NOT NULL, punti INTEGER NOT NULL DEFAULT 0,
        UNIQUE(id_utente, giornata), FOREIGN KEY(id_utente) REFERENCES utenti(id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS punteggi_fase (
        id INTEGER PRIMARY KEY AUTOINCREMENT, id_utente INTEGER NOT NULL,
        fase TEXT NOT NULL, punti INTEGER NOT NULL DEFAULT 0,
        UNIQUE(id_utente, fase), FOREIGN KEY(id_utente) REFERENCES utenti(id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS stato_giornata (
        id INTEGER PRIMARY KEY AUTOINCREMENT, giornata INTEGER NOT NULL UNIQUE,
        is_attiva BOOLEAN NOT NULL DEFAULT 0, is_in_archivio BOOLEAN NOT NULL DEFAULT 0)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS stato_fase (
        id INTEGER PRIMARY KEY AUTOINCREMENT, fase TEXT NOT NULL UNIQUE,
        is_attiva BOOLEAN NOT NULL DEFAULT 0, is_in_archivio BOOLEAN NOT NULL DEFAULT 0,
        pronostici_locked BOOLEAN NOT NULL DEFAULT 0)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS stato_pronostici_torneo (
        id INTEGER PRIMARY KEY, is_locked BOOLEAN NOT NULL DEFAULT 0)""")
    conn.execute("INSERT OR IGNORE INTO stato_pronostici_torneo (id,is_locked) VALUES (1,0)")
    conn.execute("""CREATE TABLE IF NOT EXISTS risultati_torneo (
        id INTEGER PRIMARY KEY, vincitore TEXT, finalista TEXT,
        semifinalista_1 TEXT, semifinalista_2 TEXT, capocannoniere TEXT)""")
    conn.execute("INSERT OR IGNORE INTO risultati_torneo (id) VALUES (1)")
    conn.execute("""CREATE TABLE IF NOT EXISTS giocatori (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nome_giocatore TEXT NOT NULL,
        squadra TEXT NOT NULL)""")


def _migrate_schema(conn):
    if USE_POSTGRES:
        try:
            db_execute(conn, "ALTER TABLE utenti ADD COLUMN IF NOT EXISTS email TEXT")
            db_execute(conn, "ALTER TABLE utenti ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE")
            db_execute(conn, "ALTER TABLE partite ADD COLUMN IF NOT EXISTS fase TEXT NOT NULL DEFAULT 'gironi'")
            db_execute(conn, "ALTER TABLE partite ADD COLUMN IF NOT EXISTS girone TEXT")
            db_execute(conn, "ALTER TABLE partite ADD COLUMN IF NOT EXISTS gol_casa_90 INTEGER")
            db_execute(conn, "ALTER TABLE partite ADD COLUMN IF NOT EXISTS gol_ospite_90 INTEGER")
            db_execute(conn, "ALTER TABLE partite ADD COLUMN IF NOT EXISTS vincitore TEXT")
        except Exception:
            log.exception("Errore migrazione schema postgres")
    else:
        cur = conn.execute("PRAGMA table_info(utenti)")
        cols = {r[1] for r in cur.fetchall()}
        for col, defn in [('email', 'TEXT'), ('is_admin', 'BOOLEAN NOT NULL DEFAULT 0')]:
            if col not in cols:
                try:
                    conn.execute(f"ALTER TABLE utenti ADD COLUMN {col} {defn}")
                except Exception:
                    pass
        cur = conn.execute("PRAGMA table_info(partite)")
        pcols = {r[1] for r in cur.fetchall()}
        for col, defn in [
            ('fase', "TEXT NOT NULL DEFAULT 'gironi'"),
            ('girone', 'TEXT'), ('gol_casa_90', 'INTEGER'),
            ('gol_ospite_90', 'INTEGER'), ('vincitore', 'TEXT')
        ]:
            if col not in pcols:
                try:
                    conn.execute(f"ALTER TABLE partite ADD COLUMN {col} {defn}")
                except Exception:
                    pass


def _promuovi_admin_storico(conn):
    cond = "is_admin = TRUE" if USE_POSTGRES else "is_admin = 1"
    row  = db_fetchone(conn, f"SELECT COUNT(*) AS c FROM utenti WHERE {cond}")
    if (row_get(row, 'c') or 0) == 0:
        legacy = os.environ.get('LEGACY_ADMIN_USERNAME', 'mirko')
        flag   = "TRUE" if USE_POSTGRES else "1"
        db_execute(conn, f"UPDATE utenti SET is_admin = {flag} WHERE nome_utente = ?", (legacy,))
        log.info(f"Promosso '{legacy}' ad admin.")


def create_tables():
    with db_conn() as conn:
        if USE_POSTGRES:
            _create_tables_postgres(conn)
        else:
            _create_tables_sqlite(conn)
        _migrate_schema(conn)
        _promuovi_admin_storico(conn)
        db_commit(conn)


# ─── Filtri Jinja ─────────────────────────────────────────────────────────────

def _register_filters(app):
    @app.template_filter('datetime_local')
    def datetime_local(s):
        if not s:
            return ''
        try:
            naive = parse_flexible_datetime(str(s))
            if not naive:
                return str(s)
            roma_tz = pytz.timezone('Europe/Rome')
            return pytz.utc.localize(naive).astimezone(roma_tz).strftime('%d/%m %H:%M')
        except Exception:
            return str(s)

    @app.template_filter('datetime_form')
    def datetime_form(s):
        if not s:
            return ''
        try:
            naive = parse_flexible_datetime(str(s))
            if not naive:
                return str(s)
            roma_tz = pytz.timezone('Europe/Rome')
            return pytz.utc.localize(naive).astimezone(roma_tz).strftime('%Y-%m-%dT%H:%M')
        except Exception:
            return str(s)


# ─── Factory ──────────────────────────────────────────────────────────────────

def create_app(config=None) -> Flask:
    app = Flask(__name__)
    cfg = config or get_config()

    app.secret_key = cfg.SECRET_KEY
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=cfg.PERMANENT_SESSION_LIFETIME_DAYS)
    app.config['SESSION_COOKIE_HTTPONLY'] = cfg.SESSION_COOKIE_HTTPONLY
    app.config['SESSION_COOKIE_SAMESITE'] = cfg.SESSION_COOKIE_SAMESITE
    app.config['SESSION_COOKIE_SECURE']   = getattr(cfg, 'SESSION_COOKIE_SECURE', False)
    app.config['WTF_CSRF_TIME_LIMIT']     = cfg.WTF_CSRF_TIME_LIMIT
    app.config['RATELIMIT_STORAGE_URI']   = cfg.RATELIMIT_STORAGE_URI
    app.config['FOOTBALL_API_KEY']  = cfg.FOOTBALL_API_KEY
    app.config['FOOTBALL_API_BASE'] = cfg.FOOTBALL_API_BASE
    app.config['COMPETITION_CODE']  = cfg.COMPETITION_CODE
    app.config['RESEND_API_KEY']    = cfg.RESEND_API_KEY
    app.config['EMAIL_FROM_NAME']   = cfg.EMAIL_FROM_NAME
    app.config['EMAIL_FROM_ADDRESS'] = cfg.EMAIL_FROM_ADDRESS
    app.config['APP_URL']           = cfg.APP_URL

    db_url = getattr(cfg, 'DATABASE_URL', '')
    if db_url:
        if db_url.startswith('postgres://'):
            db_url = db_url.replace('postgres://', 'postgresql://', 1)
        app.config['SQLALCHEMY_DATABASE_URI'] = db_url
    else:
        db_path = os.path.join(app.root_path, 'database.db')
        app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    csrf.init_app(app)
    limiter.init_app(app)
    db.init_app(app)

    if getattr(cfg, 'TALISMAN_ENABLED', False):
        Talisman(app, content_security_policy=CSP, force_https=True,
                 strict_transport_security=True, strict_transport_security_max_age=31536000,
                 frame_options='DENY', referrer_policy='strict-origin-when-cross-origin')
    else:
        @app.after_request
        def _headers(response):
            response.headers.setdefault('X-Frame-Options', 'DENY')
            response.headers.setdefault('X-Content-Type-Options', 'nosniff')
            return response

    _register_filters(app)

    @app.context_processor
    def inject_globals():
        g_attiva = None
        fase_attiva = None
        try:
            with db_conn() as conn:
                row = db_fetchone(conn, 'SELECT giornata FROM stato_giornata WHERE is_attiva=TRUE')
                g_attiva = row_get(row, 'giornata') if row else None
                row2 = db_fetchone(conn, 'SELECT fase FROM stato_fase WHERE is_attiva=TRUE')
                fase_attiva = row_get(row2, 'fase') if row2 else None
        except Exception:
            log.exception('Errore lettura stato attivo')
        return {
            'giornata_attiva': g_attiva,
            'fase_attiva':     fase_attiva,
            'csrf_token':      generate_csrf,
            'is_admin':        bool(session.get('is_admin')),
            'APP_NAME':        'Fanta Mondiali 2026',
        }

    from blueprints.auth  import auth_bp
    from blueprints.gioco import gioco_bp
    from blueprints.admin import admin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(gioco_bp)
    app.register_blueprint(admin_bp)

    with app.app_context():
        try:
            create_tables()
        except Exception:
            log.exception('ERRORE create_tables')

    # APScheduler — reminder automatici ogni 30 minuti
    if not app.config.get('TESTING'):
        from services.game_logic import invia_reminder_automatici
        scheduler = BackgroundScheduler(timezone='Europe/Rome')
        scheduler.add_job(
            func=invia_reminder_automatici,
            args=[app],
            trigger='interval',
            minutes=30,
            id='reminder_auto',
            replace_existing=True,
        )
        scheduler.start()
        log.info('[SCHEDULER] APScheduler avviato — reminder ogni 30 minuti.')

    return app


app = create_app()

if __name__ == '__main__':
    debug_mode = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(host='127.0.0.1', port=5001, debug=debug_mode)
