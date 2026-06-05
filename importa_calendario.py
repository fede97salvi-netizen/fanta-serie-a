"""
Importa il calendario Serie A da football-data.org nel database SQLite locale.

Uso:
    export FOOTBALL_API_KEY="la_tua_chiave"
    python scripts/importa_calendario.py
"""

import os
import sqlite3
import sys

import requests

DATABASE_FILE = 'database.db'
API_KEY = os.environ.get('FOOTBALL_API_KEY', '')
API_URL = 'https://api.football-data.org/v4/competitions/SA/matches'


def assicura_api_key():
    if not API_KEY:
        sys.exit(
            "❌ Variabile d'ambiente FOOTBALL_API_KEY non impostata.\n"
            "   Setta la chiave con:  export FOOTBALL_API_KEY='...'"
        )


def pulizia_totale():
    """Cancella le partite importate, lasciando le prime 4 (dati di prova)."""
    print("--- 1. PULIZIA DATABASE ---")
    if not os.path.exists(DATABASE_FILE):
        print(f"❌ File database '{DATABASE_FILE}' non trovato.")
        return False
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM partite WHERE id > 4")
            conn.commit()
            print(f"✅ Pulizia completata: {cursor.rowcount} partite eliminate.")
            return True
        finally:
            conn.close()
    except sqlite3.Error as e:
        print(f"❌ Errore SQLite: {e}")
        return False


def importa_calendario():
    print("--- 2. IMPORT CALENDARIO ---")
    headers = {'X-Auth-Token': API_KEY}
    try:
        resp = requests.get(API_URL, headers=headers, timeout=20)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"❌ Errore API: {e}")
        return

    matches = resp.json().get('matches', [])
    print(f"📥 Ricevute {len(matches)} partite dall'API.")

    conn = sqlite3.connect(DATABASE_FILE)
    try:
        cur = conn.cursor()
        inserite = 0
        for m in matches:
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
        print(f"✅ Importate {inserite} partite.")
    finally:
        conn.close()


if __name__ == '__main__':
    assicura_api_key()
    if pulizia_totale():
        importa_calendario()
