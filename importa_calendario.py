# importa_calendario.py

import sqlite3
import os
import requests # Importiamo la libreria per le richieste web

# --- CONFIGURAZIONE ---
DATABASE_FILE = 'database.db'
API_KEY = 'e4e0aa85c71b4e7db090e0415e2c1bba'
# Questo è l'endpoint per le partite della Serie A (ID Competizione: SA)
API_URL = 'https://api.football-data.org/v4/competitions/SA/matches'
# --------------------

def pulizia_totale():
    """Cancella tutte le partite importate in precedenza per evitare duplicati."""
    print("--- 1. INIZIO PULIZIA DATABASE ---")
    if not os.path.exists(DATABASE_FILE):
        print(f"ERRORE: File database '{DATABASE_FILE}' non trovato.")
        return False
        
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        # Lasciamo intatte le 4 partite di prova iniziali (se esistono)
        cursor.execute("DELETE FROM partite WHERE id > 4")
        conn.commit()
        print(f"✅ Pulizia completata: {cursor.rowcount} partite importate sono state eliminate.")
        return True
    except sqlite3.Error as e:
        print(f"❌ ERRORE durante la pulizia del database: {e}")
        return False
    finally:
        if conn:
            conn.close()

def importa_calendario():
    """Importa il calendario aggiornato della Serie A tramite API."""
    print("\n--- 2. INIZIO IMPORTAZIONE CALENDARIO ---")
    
    # Prepariamo la richiesta, includendo la nostra chiave API nell'header
    headers = {'X-Auth-Token': API_KEY}
    
    try:
        # Eseguiamo la chiamata alla API
        response = requests.get(API_URL, headers=headers)
        # Controlliamo se la richiesta è andata a buon fine (codice 200 significa OK)
        response.raise_for_status() 
        print("✅ Connessione alla API riuscita.")

        # Convertiamo i dati ricevuti (in formato JSON) in un dizionario Python
        data = response.json()
        partite = data.get('matches', [])
        
        if not partite:
            print("⚠️ Nessuna partita trovata nella risposta della API.")
            return

        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        
        partite_inserite = 0
        for partita in partite:
            giornata = partita['matchday']
            squadra_casa = partita['homeTeam']['shortName'].upper()
            squadra_ospite = partita['awayTeam']['shortName'].upper()
            # La data è in formato UTC, la nostra app la convertirà nel fuso orario giusto
            data_ora_utc = partita['utcDate'].replace('Z', '') 
            
            # Valori che per ora non abbiamo: risultato, marcatore, pronosticabile
            # Impostiamo pronosticabile=True di default
            cursor.execute("""
                INSERT INTO partite (giornata, squadra_casa, squadra_ospite, pronosticabile, data_ora_partita) 
                VALUES (?, ?, ?, ?, ?)
            """, (giornata, squadra_casa, squadra_ospite, True, data_ora_utc))
            partite_inserite += 1
            
        conn.commit()
        print(f"✅ Importazione completata: {partite_inserite} partite sono state inserite nel database.")

    except requests.exceptions.RequestException as e:
        print(f"❌ ERRORE di connessione alla API: {e}")
    except sqlite3.Error as e:
        print(f"❌ ERRORE durante l'inserimento nel database: {e}")
    finally:
        if 'conn' in locals() and conn:
            conn.close()
    
    print("---------------------------------------")


if __name__ == '__main__':
    if pulizia_totale():
        importa_calendario()