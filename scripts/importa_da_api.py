"""
Importa calendario stagione corrente da football-data.org nel DB SQLite locale.

Uso:
    export FOOTBALL_API_KEY="la_tua_chiave"
    python scripts/importa_da_api.py
"""

import os
import sqlite3
import sys

import requests

DATABASE_FILE = 'database.db'
API_KEY = os.environ.get('FOOTBALL_API_KEY', '')
SEASON = os.environ.get('SEASON', '2025')  # 2025 = stagione 2025/2026


def importa_e_aggiorna_calendario():
    if not API_KEY:
        sys.exit("❌ Variabile d'ambiente FOOTBALL_API_KEY non impostata.")

    url = "https://api.football-data.org/v4/competitions/SA/matches"
    headers = {'X-Auth-Token': API_KEY}
    params = {'season': SEASON}

    print(f"📡 Scarico calendario stagione {SEASON}/{int(SEASON) + 1}...")
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=20)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"❌ Errore API: {e}")
        return

    data = resp.json()
    if not data.get('matches'):
        print("⚠️ Nessuna partita trovata.")
        return

    conn = sqlite3.connect(DATABASE_FILE)
    try:
        cur = conn.cursor()
        inserite = 0
        for m in data['matches']:
            cur.execute(
                "INSERT INTO partite (giornata, squadra_casa, squadra_ospite, pronosticabile, data_ora_partita) "
                "VALUES (?, ?, ?, 0, ?)",
                (
                    m.get('matchday'),
                    (m['homeTeam']['name'] or '').upper(),
                    (m['awayTeam']['name'] or '').upper(),
                    m.get('utcDate'),
                ),
            )
            inserite += 1
        conn.commit()
        print(f"✅ Inserite {inserite} partite.")
    finally:
        conn.close()


if __name__ == '__main__':
    importa_e_aggiorna_calendario()
