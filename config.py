"""
Configurazione centralizzata.
Seleziona automaticamente DevelopmentConfig o ProductionConfig
in base alla presenza di DATABASE_URL / RENDER.
"""

import os
import sys
import secrets
import logging


def _resolve_secret_key() -> str:
    key = os.environ.get('SECRET_KEY')
    if key:
        return key
    if os.environ.get('RENDER') or os.environ.get('DATABASE_URL'):
        logging.getLogger('fanta').error(
            'SECRET_KEY non impostata in produzione. Uscita forzata.')
        sys.exit(1)
    keyfile = os.path.join(os.path.dirname(__file__), '.local_secret_key')
    if os.path.exists(keyfile):
        with open(keyfile) as f:
            return f.read().strip()
    new_key = secrets.token_hex(32)
    with open(keyfile, 'w') as f:
        f.write(new_key)
    logging.getLogger('fanta').warning(
        'Generata SECRET_KEY locale in .local_secret_key (gitignored).')
    return new_key


class BaseConfig:
    SECRET_KEY: str = _resolve_secret_key()
    WTF_CSRF_TIME_LIMIT = None          # token valido per tutta la sessione
    PERMANENT_SESSION_LIFETIME_DAYS = 30
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'

    # Football-Data API
    FOOTBALL_API_KEY: str = os.environ.get('FOOTBALL_API_KEY', '')
    FOOTBALL_API_BASE = 'https://api.football-data.org/v4'
    SERIE_A_CODE = 'SA'

    # Email
    RESEND_API_KEY: str = os.environ.get('RESEND_API_KEY', '')
    EMAIL_FROM_NAME = 'FantaSerieA'
    EMAIL_FROM_ADDRESS: str = os.environ.get(
        'EMAIL_FROM_ADDRESS', 'onboarding@resend.dev')
    APP_URL: str = os.environ.get(
        'APP_URL', 'https://fanta-serie-a-1.onrender.com')

    # Gioco
    MIN_PASSWORD_LEN = 6

    # Limiter
    RATELIMIT_STORAGE_URI = 'memory://'
    RATELIMIT_DEFAULT = []

    # Logging
    LOG_LEVEL: str = os.environ.get('LOG_LEVEL', 'INFO')


class DevelopmentConfig(BaseConfig):
    DEBUG = True
    SESSION_COOKIE_SECURE = False
    # DATABASE_URL non impostato → SQLite locale
    DATABASE_URL: str = ''
    # Talisman disabilitato in locale (no HTTPS)
    TALISMAN_ENABLED = False


class ProductionConfig(BaseConfig):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    DATABASE_URL: str = os.environ.get('DATABASE_URL', '')
    TALISMAN_ENABLED = True

    # Rinomina postgres:// in postgresql:// se necessario (Render legacy)
    def __init_subclass__(cls) -> None:
        super().__init_subclass__()

    @classmethod
    def fix_db_url(cls, url: str) -> str:
        if url.startswith('postgres://'):
            return url.replace('postgres://', 'postgresql://', 1)
        return url


def get_config() -> BaseConfig:
    database_url = os.environ.get('DATABASE_URL', '')
    is_prod = bool(database_url or os.environ.get('RENDER'))
    return ProductionConfig() if is_prod else DevelopmentConfig()
