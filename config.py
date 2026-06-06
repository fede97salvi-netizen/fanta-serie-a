"""Configurazione Fanta Mondiali 2026."""

import os, sys, secrets, logging

APP_NAME = "Fanta Mondiali 2026"
COMPETITION_CODE = "WC"   # football-data.org code for FIFA World Cup


def _resolve_secret_key() -> str:
    key = os.environ.get('SECRET_KEY')
    if key:
        return key
    if os.environ.get('RENDER') or os.environ.get('DATABASE_URL'):
        logging.getLogger('mondiali').error("SECRET_KEY non impostata in produzione.")
        sys.exit(1)
    keyfile = os.path.join(os.path.dirname(__file__), '.local_secret_key')
    if os.path.exists(keyfile):
        with open(keyfile) as f:
            return f.read().strip()
    new_key = secrets.token_hex(32)
    with open(keyfile, 'w') as f:
        f.write(new_key)
    logging.getLogger('mondiali').warning("Generata SECRET_KEY locale in .local_secret_key")
    return new_key


class BaseConfig:
    SECRET_KEY: str = _resolve_secret_key()
    WTF_CSRF_TIME_LIMIT = None
    PERMANENT_SESSION_LIFETIME_DAYS = 30
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    FOOTBALL_API_KEY: str = os.environ.get('FOOTBALL_API_KEY', '')
    FOOTBALL_API_BASE = 'https://api.football-data.org/v4'
    COMPETITION_CODE = COMPETITION_CODE
    RESEND_API_KEY: str = os.environ.get('RESEND_API_KEY', '')
    EMAIL_FROM_NAME = APP_NAME
    EMAIL_FROM_ADDRESS: str = os.environ.get('EMAIL_FROM_ADDRESS', 'onboarding@resend.dev')
    APP_URL: str = os.environ.get('APP_URL', 'https://fanta-mondiali-2026.onrender.com')
    MIN_PASSWORD_LEN = 6
    RATELIMIT_STORAGE_URI = 'memory://'
    RATELIMIT_DEFAULT = []
    LOG_LEVEL: str = os.environ.get('LOG_LEVEL', 'INFO')


class DevelopmentConfig(BaseConfig):
    DEBUG = True
    SESSION_COOKIE_SECURE = False
    DATABASE_URL: str = ''
    TALISMAN_ENABLED = False


class ProductionConfig(BaseConfig):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    DATABASE_URL: str = os.environ.get('DATABASE_URL', '')
    TALISMAN_ENABLED = True


def get_config() -> BaseConfig:
    is_prod = bool(os.environ.get('DATABASE_URL') or os.environ.get('RENDER'))
    return ProductionConfig() if is_prod else DevelopmentConfig()
