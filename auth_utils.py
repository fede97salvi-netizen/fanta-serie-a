"""
Decoratori di autorizzazione condivisi.

Evitano la ripetizione del controllo di sessione/ruolo in ogni route
(riduce il rischio di dimenticare un guard su una nuova route).
"""

from functools import wraps

from flask import session, redirect, url_for

from db_utils import db_conn, db_fetchone


def _utente_esiste(nome_utente: str) -> bool:
    with db_conn() as conn:
        return db_fetchone(conn, 'SELECT id FROM utenti WHERE nome_utente = ?',
                           (nome_utente,)) is not None


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if 'nome_utente' not in session:
            return redirect(url_for('auth.login'))
        # Utente rinominato o eliminato mentre era collegato: la sessione
        # punta a un nome che non esiste piu'. Si chiude e si torna al login.
        if not _utente_esiste(session['nome_utente']):
            session.clear()
            return redirect(url_for('auth.login'))
        return view(*args, **kwargs)
    return wrapper


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if 'nome_utente' not in session or not session.get('is_admin'):
            return 'Accesso negato.', 403
        return view(*args, **kwargs)
    return wrapper
