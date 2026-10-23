"""
FantaSerieA — app factory (V3)

Crea e configura l'applicazione Flask.
Chiamare create_app() per ottenere un'istanza configurata.
"""

import logging
import os
import json
from datetime import timedelta, datetime
import pytz
from flask import Flask, session, g as flask_g, render_template, request, jsonify, send_from_directory
from flask_wtf.csrf import generate_csrf
from flask_talisman import Talisman
from werkzeug.middleware.proxy_fix import ProxyFix

from config import get_config
from auth_utils import admin_required
from extensions import csrf, limiter, db
from db_utils import db_conn, db_execute, db_fetchone, db_fetchall, db_commit, row_get, USE_POSTGRES
from services.game_logic import parse_flexible_datetime, pulisci_username
from db_schema import aggiorna_schema
from invia_notifiche import (
    invia_promemoria_generale, invia_promemoria_partite,
    invia_promemoria_scadenza, salva_subscription_push,
)

# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=os.environ.get('LOG_LEVEL', 'INFO'),
    format='[%(asctime)s] %(levelname)s %(name)s — %(message)s',
)
log = logging.getLogger('fanta')

# ─── CSP (Content Security Policy) ───────────────────────────────────────────
CSP = {
    'default-src':  "'self'",
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

# Lo schema (tabelle, colonne, indici) e' gestito solo da Alembic: vedi
# db_schema.py e migrations/. Qui restano i dati minimi che l'app si aspetta.

def _dati_di_base(conn):
    """Righe singole che l'app legge sempre (id=1). Idempotente."""
    db_execute(conn, "INSERT INTO stato_pronostici_iniziali (id, is_locked) VALUES (1, ?) "
                     "ON CONFLICT (id) DO NOTHING", (False,))
    db_execute(conn, "INSERT INTO risultati_finali (id) VALUES (1) ON CONFLICT (id) DO NOTHING")


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

def _pulisci_username_spazi(conn):
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

def prepara_database():
    aggiorna_schema()
    with db_conn() as conn:
        _dati_di_base(conn)
        _promuovi_admin_storico(conn)
        _pulisci_username_spazi(conn)
        db_commit(conn)
    from services.inviti import assicura_codice_invito
    assicura_codice_invito()

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

def _inizializza_sentry():
    """Segnala a Sentry eccezioni non gestite e log di livello ERROR.

    Attivo solo se SENTRY_DSN e' impostata (su Render): in locale e nei test
    non parte nulla. Niente dati personali: ne' IP/cookie ne' corpo delle
    richieste (i form di login contengono password).
    """
    dsn = os.environ.get('SENTRY_DSN')
    if not dsn:
        return False
    import sentry_sdk
    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get('SENTRY_ENVIRONMENT', 'production'),
        release=os.environ.get('RENDER_GIT_COMMIT'),
        send_default_pii=False,
        max_request_body_size='never',
        traces_sample_rate=0.0,
    )
    return True


def create_app(config=None) -> Flask:
    _inizializza_sentry()
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

    # SQLAlchemy
    db_url = getattr(cfg, 'DATABASE_URL', '')
    if db_url:
        if db_url.startswith('postgres://'):
            db_url = db_url.replace('postgres://', 'postgresql://', 1)
        app.config['SQLALCHEMY_DATABASE_URI'] = db_url
    else:
        db_path = os.path.join(app.root_path, 'database.db')
        app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Su Render l'app sta dietro un proxy: senza ProxyFix request.remote_addr
    # e' l'IP del proxy per tutti, e il rate limit per IP diventa un unico
    # contatore condiviso da tutti gli utenti.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    # Estensioni
    csrf.init_app(app)
    limiter.init_app(app)
    db.init_app(app)

    # Flask-Talisman
    talisman_enabled = getattr(cfg, 'TALISMAN_ENABLED', False)
    if talisman_enabled:
        Talisman(
            app,
            content_security_policy=CSP,
            force_https=True,
            strict_transport_security=True,
            strict_transport_security_max_age=31536000,
            strict_transport_security_include_subdomains=True,
            frame_options='DENY',
            referrer_policy='strict-origin-when-cross-origin',
        )
    else:
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
    _register_notifiche_routes(app)

    with app.app_context():
        try:
            prepara_database()
        except Exception:
            log.exception('ERRORE preparazione database')

    return app

# ─── Rotte notifiche push e cron ─────────────────────────────────────────────
# Registrate dentro la factory: cosi' esistono in ogni istanza dell'app,
# compresa quella dei test.

def _register_notifiche_routes(app: Flask):
    # ROTTA PER SERVIRE IL SERVICE WORKER
    @app.route('/sw.js')
    def serve_sw():
        return send_from_directory(app.root_path, 'sw.js', mimetype='application/javascript')

    # ROTTA PER SALVARE LE ISCRIZIONI PUSH
    # Protetta da CSRF: la pagina manda il token nell'header X-CSRFToken.
    # Solo per utenti loggati: ogni dispositivo viene legato al suo utente.
    @app.route('/salva_iscrizione_push', methods=['POST'])
    def salva_iscrizione_push():
        if 'nome_utente' not in session:
            return jsonify({'status': 'error', 'error': 'Accedi per attivare le notifiche'}), 401
        subscription = request.get_json(silent=True)
        if not isinstance(subscription, dict) or not subscription.get('endpoint'):
            return jsonify({'status': 'error', 'error': 'Iscrizione non valida'}), 400
        try:
            if not salva_subscription_push(session['nome_utente'], subscription):
                return jsonify({'status': 'error', 'error': 'Utente non trovato'}), 401
        except Exception:
            log.exception('Errore salvataggio iscrizione push')
            return jsonify({'status': 'error', 'error': 'Errore salvataggio sul server'}), 500

        return jsonify({'status': 'success'})

    # ROTTE DI TEST DELLE NOTIFICHE: solo per l'admin, perché inviano a tutti.
    @app.route('/test_spara_notifica')
    @admin_required
    def test_spara_notifica():
        esito = invia_promemoria_generale(
            "FantaSerieA: Sveglia! ⏰",
            "Se leggi questo messaggio, il test via GitHub è andato a buon fine!"
        )
        return esito

    # ROTTA DI TEST PER IL MESSAGGIO DI PROMEMORIA 30' PRIMA
    # Manda solo la notifica di prova, senza creare o toccare nessuna partita.
    @app.route('/test_promemoria_partite')
    @admin_required
    def test_promemoria_partite():
        esito = invia_promemoria_generale(
            "⏰ Manca mezz'ora!",
            "Questo è un test del promemoria pre-partita: se lo ricevi, il sistema funziona."
        )
        return esito

    # ROTTA DI TEST PER L'ALERT "ULTIMI MINUTI, MANCA IL PRONOSTICO"
    @app.route('/test_promemoria_scadenza')
    @admin_required
    def test_promemoria_scadenza():
        esito = invia_promemoria_generale(
            "⚠️ Ultimi minuti!",
            "Questo è un test dell'alert 'manca poco e non hai ancora pronosticato': "
            "se lo ricevi, il sistema funziona."
        )
        return esito

    # ROTTA CRON: promemoria automatico 30 minuti prima di ogni partita, a tutti
    # gli iscritti, chiamata periodicamente da GitHub Actions/cron-job.org.
    @app.route('/cron/invia_promemoria_partite', methods=['POST'])
    @csrf.exempt
    def cron_invia_promemoria_partite():
        secret_atteso = os.environ.get('CRON_SECRET')
        if not secret_atteso or request.headers.get('X-Cron-Secret') != secret_atteso:
            return jsonify({'status': 'error', 'error': 'non autorizzato'}), 403

        fonte = request.args.get('fonte', 'sconosciuta')
        esito = invia_promemoria_partite()
        log.info(f"[cron promemoria] (fonte: {fonte}) {esito}")
        return jsonify({'status': 'ok', 'fonte': fonte, 'esito': esito})

    # ROTTA CRON: alert "ultimi minuti" solo a chi non ha ancora inserito il
    # pronostico. Rotta separata dal promemoria dei 30' apposta: così può girare
    # su un cron dedicato, con una cadenza più stretta (es. ogni 5-10 minuti),
    # senza toccare né la cadenza né il comportamento di quello già in uso.
    @app.route('/cron/invia_promemoria_scadenza', methods=['POST'])
    @csrf.exempt
    def cron_invia_promemoria_scadenza():
        secret_atteso = os.environ.get('CRON_SECRET')
        if not secret_atteso or request.headers.get('X-Cron-Secret') != secret_atteso:
            return jsonify({'status': 'error', 'error': 'non autorizzato'}), 403

        fonte = request.args.get('fonte', 'sconosciuta')
        esito = invia_promemoria_scadenza()
        log.info(f"[cron promemoria scadenza] (fonte: {fonte}) {esito}")
        return jsonify({'status': 'ok', 'fonte': fonte, 'esito': esito})

# ─── Entry point (locale / gunicorn) ─────────────────────────────────────────

app = create_app()

if __name__ == '__main__':
    debug_mode = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(host='127.0.0.1', port=5000, debug=debug_mode)
