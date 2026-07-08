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

# Importa SOLO i metadata dei modelli per l'autogenerazione.
# NB: non importare 'app' qui: app.py esegue create_app() a import-time
# (che crea le tabelle) e renderebbe vuoto l'autogenerate.
from extensions import db
import models  # noqa: F401 — registra i modelli su db.metadata

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
    """Applica migrazioni live sul DB (engine costruito dall'URL, senza app)."""
    connectable = engine_from_config(
        {'sqlalchemy.url': get_url()},
        prefix='sqlalchemy.',
        poolclass=pool.NullPool,
    )
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
