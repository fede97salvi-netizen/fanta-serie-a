import sqlite3
import requests
import os
from datetime import datetime

# --- Configurazione ---
DATABASE_FILE = 'database.db'
API_KEY = "e4e0aa85c71b4e7db090e0415e2c1bba"
# --------------------

def importa_e_aggiorna_calendario():
    url = "https://api.football-data.org/v4/competitions/SA/matches"
    headers = { 'X-Auth-Token': API_KEY }
    
    # --- LA CORREZIONE È QUI SOTTO ---
    # Siamo a Nov 2025, quindi la stagione è la '2025' (che copre 2025/2026)
    params = { 'season': '2025' } 

    print("📡 Scarico il calendario della stagione 2025/2026...")
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"❌ Errore API: {e}")
        return

    data = response.json()
    if not data.get('matches'):
        print("⚠️ Nessuna partita trovata. Controlla la stagione o la chiave API.")
        return

    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row 
    cursor = conn.cursor()
    
    aggiornate = 0
    inserite = 0

    for partita_api in data['matches']:
        giornata = partita_api['matchday']
        casa = partita_api['homeTeam']['shortName'].upper()
        ospite = partita_api['awayTeam']['shortName'].upper()
        data_utc = partita_api['utcDate'] # Es: 2025-11-27T14:00:00Z
        
        # Convertiamo in formato stringa semplice per il DB
        dt_obj = datetime.strptime(data_utc, '%Y-%m-%dT%H:%M:%SZ')
        data_db = dt_obj.strftime('%Y-%m-%dT%H:%M') # Tagliamo i secondi e la Z

        # Controllo esistenza
        cursor.execute("SELECT id, data_ora_partita FROM partite WHERE giornata = ? AND squadra_casa = ? AND squadra_ospite = ?", 
                       (giornata, casa, ospite))
        esistente = cursor.fetchone()
        
        if esistente:
            # Controllo se l'orario è cambiato (confrontiamo le stringhe)
            # Prendiamo i primi 16 caratteri per sicurezza (YYYY-MM-DDTHH:MM)
            vecchia_data = esistente['data_ora_partita'][:16] if esistente['data_ora_partita'] else ""
            nuova_data = data_db[:16]

            if vecchia_data != nuova_data:
                print(f"🔄 Aggiorno G{giornata}: {casa}-{ospite} da {vecchia_data} a {nuova_data}")
                cursor.execute("UPDATE partite SET data_ora_partita = ? WHERE id = ?", (data_db, esistente['id']))
                aggiornate += 1
        else:
            print(f"➕ Inserisco G{giornata}: {casa}-{ospite} alle {data_db}")
            cursor.execute("INSERT INTO partite (giornata, squadra_casa, squadra_ospite, data_ora_partita, pronosticabile) VALUES (?, ?, ?, ?, ?)",
                           (giornata, casa, ospite, data_db, True))
            inserite += 1

    conn.commit()
    conn.close()
    print(f"\n✅ Finito! Inserite: {inserite}, Aggiornate: {aggiornate}")

if __name__ == '__main__':
    importa_e_aggiorna_calendario()