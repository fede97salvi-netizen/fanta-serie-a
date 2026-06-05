import sqlite3
import os

DATABASE_FILE = 'database.db'

def pulisci_db():
    if not os.path.exists(DATABASE_FILE):
        print("Database non trovato!")
        return

    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    print("🧹 Inizio pulizia delle partite della stagione passata...")
    
    # Cancelliamo tutte le partite che si sono giocate prima del 1° Luglio 2025
    # (Così eliminiamo la stagione 24/25 importata per sbaglio)
    cursor.execute("DELETE FROM partite WHERE data_ora_partita < '2025-07-01'")
    
    partite_cancellate = cursor.rowcount
    conn.commit()
    conn.close()
    
    print(f"✅ Fatto! Ho cancellato {partite_cancellate} partite vecchie che non c'entravano nulla.")

if __name__ == '__main__':
    pulisci_db()