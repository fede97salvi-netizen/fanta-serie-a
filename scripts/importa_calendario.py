"""
Importa il calendario Serie A da football-data.org.
Supporta sia SQLite locale che PostgreSQL (via DATABASE_URL).

Uso:
    export FOOTBALL_API_KEY="..."
    python scripts/importa_calendario.py

    # Per PostgreSQL:
    export DATABASE_URL="postgresql://..."
    export FOOTBALL_API_KEY="..."
    python scripts/importa_calendario.py
"""

import os
import sys

import requests

API_KEY = os.environ.get('FOOTBALL_API_KEY', '')
API_URL = 'https://api.football-data.org/v4/competitions/SA/matches'
DATABASE_URL = os.environ.get('DATABASE_URL', '')


def get_connection():
    if DATABASE_URL:
        import psycopg2
        import psycopg2.extras
        url = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
        return psycopg2.connect(url,
                                cursor_factory=psycopg2.extras.RealDictCursor), True
    import sqlite3
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'database.db')
    conn = sqlite3.connect(db_path)
    return conn, False


def placeholder(use_pg: bool) -> str:
    return '%s' if use_pg else '?'


def assicura_api_key():
    if not API_KEY:
        sys.exit(
            "❌ Variabile d'ambiente FOOTBALL_API_KEY non impostata.\n"
            "   Setta la chiave con:  export FOOTBALL_API_KEY='...'"
        )


def pulizia_totale():
    print("--- 1. PULIZIA DATABASE ---")
    conn, use_pg = get_connection()
    try:
        cur = conn.cursor()
        ph = placeholder(use_pg)
        cur.execute(f"DELETE FROM partite WHERE id > {ph}", (4,))
        conn.commit()
        print(f"✅ Pulizia: {cur.rowcount} partite eliminate.")
    finally:
        conn.close()


def importa_calendario():
    print("--- 2. IMPORT CALENDARIO ---")
    resp = requests.get(API_URL, headers={'X-Auth-Token': API_KEY}, timeout=20)
    resp.raise_for_status()
    matches = resp.json().get('matches', [])
    print(f"📥 Ricevute {len(matches)} partite.")

    conn, use_pg = get_connection()
    ph = placeholder(use_pg)
    try:
        cur = conn.cursor()
        inserite = 0
        for m in matches:
            cur.execute(
                f"INSERT INTO partite (giornata, squadra_casa, squadra_ospite, "
                f"pronosticabile, data_ora_partita) VALUES ({ph},{ph},{ph},{ph},{ph})",
                (m.get('matchday'),
                 (m['homeTeam']['name'] or '').upper(),
                 (m['awayTeam']['name'] or '').upper(),
                 False, m.get('utcDate')),
            )
            inserite += 1
        conn.commit()
        print(f"✅ Importate {inserite} partite.")
    finally:
        conn.close()


if __name__ == '__main__':
    assicura_api_key()
    pulizia_totale()
    importa_calendario()
