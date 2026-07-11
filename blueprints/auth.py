"""
Blueprint auth — gestione autenticazione e profilo utente.

Route:
  /                        home (welcome per visitatori, dashboard per utenti)
  /registrazione           registrazione
  /login                   login
  /logout                  logout
  /cambia-password         cambio password forzato
  /profilo                 profilo (email + cambio password volontario)
  /api/profilo-info        endpoint JSON per profilo
"""

import logging
import random
import re
import string

from flask import (
    Blueprint, render_template, request, redirect,
    url_for, session, flash,
)
from werkzeug.security import generate_password_hash, check_password_hash
from hashlib import sha256

from db_utils import db_conn, db_execute, db_fetchone, db_commit, row_get
from extensions import limiter
from services.game_logic import EMAIL_RE

log = logging.getLogger('fanta')

auth_bp = Blueprint('auth', __name__)

MIN_PASSWORD_LEN = 6


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _is_legacy_sha256(pw_hash: str) -> bool:
    return bool(pw_hash) and len(pw_hash) == 64 and all(
        c in '0123456789abcdef' for c in pw_hash.lower()
    )


def verifica_password(plain: str, stored_hash: str) -> bool:
    if not stored_hash:
        return False
    if _is_legacy_sha256(stored_hash):
        return sha256(plain.encode()).hexdigest() == stored_hash
    try:
        return check_password_hash(stored_hash, plain)
    except Exception:
        log.exception('Errore verifica password')
        return False


def hash_password(plain: str) -> str:
    return generate_password_hash(plain, method='pbkdf2:sha256', salt_length=16)


def utente_corrente(conn):
    if 'nome_utente' not in session:
        return None
    return db_fetchone(
        conn, 'SELECT * FROM utenti WHERE nome_utente = ?',
        (session['nome_utente'],),
    )


# ─── Route ──────────────────────────────────────────────────────────────────
@auth_bp.route('/', endpoint='home')
def home():
    # Visitatore non autenticato -> pagina di benvenuto
    if 'nome_utente' not in session:
        return render_template('welcome.html', session=session)
    # Utente autenticato -> dashboard home con punteggio e posizione
    with db_conn() as conn:
        g_row = db_fetchone(
            conn, 'SELECT giornata FROM stato_giornata WHERE is_attiva = TRUE')
        giornata_attiva = row_get(g_row, 'giornata') if g_row else None
        user = utente_corrente(conn)
        if not user:
            return redirect(url_for('auth.logout'))
        uid = row_get(user, 'id')
        p_row = db_fetchone(
            conn, 'SELECT punteggio_totale FROM punteggi WHERE id_utente = ?',
            (uid,),
        )
        punteggio_utente = row_get(p_row, 'punteggio_totale') or 0
        rank_row = db_fetchone(
            conn,
            'SELECT COUNT(id) + 1 AS rank FROM punteggi '
            'WHERE punteggio_totale > ?',
            (punteggio_utente,),
        )
        posizione_utente = row_get(rank_row, 'rank') or 1
    return render_template(
        'home.html',
        giornata_attiva=giornata_attiva,
        punteggio_utente=punteggio_utente,
        posizione_utente=posizione_utente,
        session=session,
    )


@auth_bp.route('/registrazione', methods=['GET', 'POST'], endpoint='registrazione')
@limiter.limit('10 per hour', methods=['POST'])
def registrazione():
    if request.method == 'POST':
        nome_utente = (request.form.get('nome_utente') or '').strip()
        password    = request.form.get('password') or ''
        if len(nome_utente) < 2:
            return render_template('registrazione.html', session=session,
                                   errore='Nome utente troppo corto.')
        if len(password) < MIN_PASSWORD_LEN:
            return render_template(
                'registrazione.html', session=session,
                errore=f'La password deve avere almeno {MIN_PASSWORD_LEN} caratteri.',
            )
        try:
            with db_conn() as conn:
                if db_fetchone(conn,
                               'SELECT id FROM utenti WHERE nome_utente = ?',
                               (nome_utente,)):
                    return render_template(
                        'registrazione.html', session=session,
                        errore='Nome utente già esistente. Scegli un altro nome.',
                    )
                db_execute(
                    conn,
                    'INSERT INTO utenti (nome_utente, password) VALUES (?, ?)',
                    (nome_utente, hash_password(password)),
                )
                db_commit(conn)
            session['nome_utente'] = nome_utente
            session['is_admin']    = False
            return redirect(url_for('auth.home'))
        except Exception:
            log.exception('Errore registrazione')
            return render_template('registrazione.html', session=session,
                                   errore='Errore durante la registrazione. Riprova.')
    return render_template('registrazione.html', session=session)


@auth_bp.route('/login', methods=['GET', 'POST'], endpoint='login')
@limiter.limit('5 per minute; 30 per hour', methods=['POST'])
def login():
    if request.method == 'POST':
        nome_utente = (request.form.get('nome_utente') or '').strip()
        password    = request.form.get('password') or ''
        with db_conn() as conn:
            user = db_fetchone(
                conn, 'SELECT * FROM utenti WHERE nome_utente = ?',
                (nome_utente,),
            )
            if not user or not verifica_password(password,
                                                  row_get(user, 'password')):
                return render_template('login.html', session=session,
                                       errore='Credenziali non valide. Riprova.')
            # Migrazione trasparente SHA-256 -> PBKDF2
            if _is_legacy_sha256(row_get(user, 'password')):
                try:
                    db_execute(
                        conn,
                        'UPDATE utenti SET password = ? WHERE id = ?',
                        (hash_password(password), row_get(user, 'id')),
                    )
                    db_commit(conn)
                    log.info(f'Migrato hash password per {nome_utente}')
                except Exception:
                    log.exception('Errore upgrade hash password')
            # Rigenera la sessione per prevenire session fixation
            is_admin_val = bool(row_get(user, 'is_admin'))
            is_temp      = row_get(user, 'is_temp_password')
            remember     = bool(request.form.get('remember'))
            session.clear()
            if remember:
                session.permanent = True
            session['nome_utente'] = nome_utente
            session['is_admin']    = is_admin_val
            if is_temp:
                return redirect(url_for('auth.cambia_password'))
        return redirect(url_for('auth.home'))
    return render_template('login.html', session=session)


@auth_bp.route('/recupera-password', methods=['GET', 'POST'],
               endpoint='recupera_password')
@limiter.limit('5 per hour', methods=['POST'])
def recupera_password():
    """Recupero password self-service (solo utenti NON admin).

    Genera una password temporanea mostrata a schermo; al primo accesso
    l'utente sara' obbligato a impostarne una nuova (is_temp_password).
    """
    if 'nome_utente' in session:
        return redirect(url_for('auth.home'))
    if request.method == 'POST':
        nome_utente = (request.form.get('nome_utente') or '').strip()
        if len(nome_utente) < 2:
            return render_template('recupera_password.html', session=session,
                                   errore='Inserisci il tuo nome utente.')
        with db_conn() as conn:
            user = db_fetchone(
                conn, 'SELECT id, is_admin FROM utenti WHERE nome_utente = ?',
                (nome_utente,),
            )
            if not user:
                return render_template('recupera_password.html', session=session,
                                       errore='Nessun utente con questo nome.')
            if row_get(user, 'is_admin'):
                return render_template(
                    'recupera_password.html', session=session,
                    errore="Per gli account amministratore contatta direttamente l'admin.")
            pw_temp = ''.join(
                random.choices(string.ascii_letters + string.digits, k=8))
            db_execute(
                conn,
                'UPDATE utenti SET password = ?, is_temp_password = TRUE '
                'WHERE id = ?',
                (hash_password(pw_temp), row_get(user, 'id')),
            )
            db_commit(conn)
            log.info(f'Reset password self-service per {nome_utente}')
        return render_template('recupera_password.html', session=session,
                               nome_utente=nome_utente, password_temp=pw_temp)
    return render_template('recupera_password.html', session=session)


@auth_bp.route('/logout', endpoint='logout')
def logout():
    session.pop('nome_utente', None)
    session.pop('is_admin', None)
    return redirect(url_for('auth.home'))


@auth_bp.route('/cambia-password', methods=['GET', 'POST'],
               endpoint='cambia_password')
def cambia_password():
    if 'nome_utente' not in session:
        return redirect(url_for('auth.login'))
    if request.method == 'POST':
        nuova_pw  = request.form.get('nuova_password') or ''
        conferma  = request.form.get('conferma_password') or ''
        if nuova_pw != conferma:
            return render_template('cambia_password.html', session=session,
                                   errore='Le password non coincidono.')
        if len(nuova_pw) < MIN_PASSWORD_LEN:
            return render_template(
                'cambia_password.html', session=session,
                errore=f'La password deve avere almeno {MIN_PASSWORD_LEN} caratteri.',
            )
        with db_conn() as conn:
            db_execute(
                conn,
                'UPDATE utenti SET password = ?, is_temp_password = FALSE '
                'WHERE nome_utente = ?',
                (hash_password(nuova_pw), session['nome_utente']),
            )
            db_commit(conn)
        return redirect(url_for('auth.home'))
    return render_template('cambia_password.html', session=session)


@auth_bp.route('/profilo', methods=['GET', 'POST'], endpoint='profilo')
def profilo():
    if 'nome_utente' not in session:
        return redirect(url_for('auth.login'))
    with db_conn() as conn:
        user         = utente_corrente(conn)
        if not user:
            return redirect(url_for('auth.logout'))
        email_attuale = row_get(user, 'email') or ''
        if request.method == 'POST':
            azione = request.form.get('azione')
            if azione == 'email':
                nuova_email = (request.form.get('nuova_email') or '').strip().lower()
                if nuova_email and not EMAIL_RE.match(nuova_email):
                    return render_template('profilo.html', email=email_attuale,
                                           session=session,
                                           errore='Formato email non valido.')
                db_execute(
                    conn,
                    'UPDATE utenti SET email = ? WHERE nome_utente = ?',
                    (nuova_email, session['nome_utente']),
                )
                db_commit(conn)
                flash('Email aggiornata.', 'success')
                return redirect(url_for('auth.profilo'))
            elif azione == 'password':
                nuova_pw = request.form.get('nuova_password') or ''
                conferma = request.form.get('conferma_password') or ''
                if nuova_pw != conferma:
                    return render_template('profilo.html', email=email_attuale,
                                           session=session,
                                           errore='Le password non coincidono.')
                if len(nuova_pw) < MIN_PASSWORD_LEN:
                    return render_template(
                        'profilo.html', email=email_attuale, session=session,
                        errore=f'La password deve avere almeno {MIN_PASSWORD_LEN} caratteri.',
                    )
                db_execute(
                    conn,
                    'UPDATE utenti SET password = ?, is_temp_password = FALSE '
                    'WHERE nome_utente = ?',
                    (hash_password(nuova_pw), session['nome_utente']),
                )
                db_commit(conn)
                flash('Password aggiornata.', 'success')
                return redirect(url_for('auth.profilo'))
    return render_template('profilo.html', email=email_attuale, session=session)


@auth_bp.route('/api/profilo-info', endpoint='api_profilo_info')
def api_profilo_info():
    if 'nome_utente' not in session:
        return {'email': ''}, 401
    with db_conn() as conn:
        user = db_fetchone(
            conn, 'SELECT email FROM utenti WHERE nome_utente = ?',
            (session['nome_utente'],),
        )
    return {'email': row_get(user, 'email') or ''}
