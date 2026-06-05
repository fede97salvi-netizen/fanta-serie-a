"""
Test di integrazione per le route Flask principali.

Usa il client di test Flask con DB SQLite in memoria (vedi conftest.py).
CSRF è disabilitato nella configurazione di test.
"""

import pytest
from tests.conftest import _crea_utente


# ─── Route pubbliche ─────────────────────────────────────────────────────────

def test_welcome_page_visitatore(client):
    """Un utente non autenticato vede la pagina di benvenuto."""
    r = client.get('/')
    assert r.status_code == 200


def test_login_page_get(client):
    r = client.get('/login')
    assert r.status_code == 200


def test_registrazione_page_get(client):
    r = client.get('/registrazione')
    assert r.status_code == 200


# ─── Registrazione ────────────────────────────────────────────────────────────

def test_registrazione_nuovo_utente(client):
    r = client.post('/registrazione',
                    data={'nome_utente': 'testuser1', 'password': 'pass123'},
                    follow_redirects=True)
    assert r.status_code == 200


def test_registrazione_password_troppo_corta(client):
    r = client.post('/registrazione',
                    data={'nome_utente': 'userx', 'password': 'abc'},
                    follow_redirects=True)
    assert r.status_code == 200
    assert 'almeno' in r.data.decode('utf-8')


def test_registrazione_nome_duplicato(client):
    _crea_utente('duplicato')
    r = client.post('/registrazione',
                    data={'nome_utente': 'duplicato', 'password': 'pass123'},
                    follow_redirects=True)
    assert 'già esistente' in r.data.decode('utf-8')


# ─── Login ────────────────────────────────────────────────────────────────────

def test_login_valido(client):
    _crea_utente('loginok', 'pass123')
    r = client.post('/login',
                    data={'nome_utente': 'loginok', 'password': 'pass123'},
                    follow_redirects=True)
    assert r.status_code == 200


def test_login_password_errata(client):
    _crea_utente('loginko', 'pass123')
    r = client.post('/login',
                    data={'nome_utente': 'loginko', 'password': 'sbagliata'},
                    follow_redirects=True)
    assert 'Credenziali non valide' in r.data.decode('utf-8')


def test_login_utente_inesistente(client):
    r = client.post('/login',
                    data={'nome_utente': 'inesistente', 'password': 'qualcosa'},
                    follow_redirects=True)
    assert 'Credenziali non valide' in r.data.decode('utf-8')


# ─── Route protette (redirect a login se non autenticato) ────────────────────

@pytest.mark.parametrize('path', [
    '/classifica',
    '/giornate',
    '/pronostici-iniziali',
    '/profilo',
])
def test_route_protetta_redirect(client, path):
    """Senza sessione attiva le route protette rimandano al login."""
    r = client.get(path)
    # Deve essere un redirect (3xx) o contenere 'login' nella location
    assert r.status_code in (301, 302)
    location = r.headers.get('Location', '')
    assert 'login' in location.lower() or r.status_code == 302


# ─── Route admin (accesso negato senza is_admin) ──────────────────────────────

def test_admin_home_senza_sessione(client):
    r = client.get('/admin')
    assert r.status_code == 403 or r.status_code == 302


def test_admin_home_con_utente_normale(client):
    """Un utente non-admin non può accedere alla dashboard admin."""
    _crea_utente('normale_user', 'pass123', is_admin=False)
    with client.session_transaction() as sess:
        sess['nome_utente'] = 'normale_user'
        sess['is_admin']    = False
    r = client.get('/admin')
    assert r.status_code == 403


def test_admin_home_con_admin(client):
    """Un utente admin può accedere."""
    _crea_utente('admin_user', 'pass123', is_admin=True)
    with client.session_transaction() as sess:
        sess['nome_utente'] = 'admin_user'
        sess['is_admin']    = True
    r = client.get('/admin')
    assert r.status_code == 200


# ─── Logout ───────────────────────────────────────────────────────────────────

def test_logout_pulisce_sessione(client):
    with client.session_transaction() as sess:
        sess['nome_utente'] = 'chiunque'
        sess['is_admin']    = False
    r = client.get('/logout', follow_redirects=False)
    assert r.status_code == 302
    with client.session_transaction() as sess:
        assert 'nome_utente' not in sess


# ─── JSON endpoint ────────────────────────────────────────────────────────────

def test_api_profilo_info_non_autenticato(client):
    r = client.get('/api/profilo-info')
    assert r.status_code == 401
