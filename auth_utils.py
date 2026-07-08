"""
Decoratori di autorizzazione condivisi.

Evitano la ripetizione del controllo di sessione/ruolo in ogni route
(riduce il rischio di dimenticare un guard su una nuova route).
"""

from functools import wraps

from flask import session, redirect, url_for


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if 'nome_utente' not in session:
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
