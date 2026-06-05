"""
Istanze delle estensioni Flask condivise tra tutti i moduli.
Vengono create qui ma inizializzate dentro create_app() con init_app().
Questo pattern evita i circular import tra app.py, blueprints e servizi.
"""

from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_sqlalchemy import SQLAlchemy

# CSRF protection — usata globalmente
csrf = CSRFProtect()

# Rate limiter — configurato via RATELIMIT_STORAGE_URI nell'app config
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
)

# SQLAlchemy — usato per i modelli (Alembic + futuro ORM) e per la connessione
# Le query esistenti usano db.session.execute(text(...)) per compatibilità
db = SQLAlchemy()
