"""
Alembic env.py — configurato per il progetto FantaSerieA.

Uso:
  # Genera migrazione da modelli
  alembic revision --autogenerate -m "descrizione"

  # Applica migrazioni
  alembic upgrade head

  # Rollback
  alembic downgrade -1
"""

import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# Aggiungi la root del progetto al sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Importa i modelli per l'autogenerazione
from app import create_app
from extensions import db
import models  # noqa: F401 — importa tutti i modelli

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# MetaData da cui Alembic genera le migrazioni
target_metadata = db.metadata


def get_url() -> str:
    url = os.environ.get('DATABASE_URL', '')
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)
    if not url:
        # Fallback SQLite locale
        db_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'database.db',
        )
        url = f'sqlite:///{db_path}'
    return url


def run_migrations_offline() -> None:
    """Genera SQL senza connettersi al DB (utile per review)."""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={'paramstyle': 'named'},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Applica migrazioni live sul DB."""
    flask_app = create_app()
    with flask_app.app_context():
        connectable = db.engine
        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                render_as_batch=True,   # necessario per SQLite ALTER TABLE
            )
            with context.begin_transaction():
                context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
