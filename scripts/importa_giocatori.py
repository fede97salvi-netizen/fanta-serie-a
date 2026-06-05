import pandas as pd
import sqlite3
import os

DATABASE_FILE = 'database.db'
CSV_FILE = 'Quotazioni_Fantacalcio_Stagione_2025_26.xlsx - Tutti.csv'
COLONNA_NOME_GIOCATORE = 'Nome' 
COLONNA_SQUADRA = 'Squadra'

def importa_giocatori_da_csv():
    print(f"Lettura del file CSV: {CSV_FILE}")
    if not os.path.exists(CSV_FILE):
        print(f"ERRORE: File '{CSV_FILE}' non trovato.")
        return

    try:
        df = pd.read_csv(CSV_FILE, encoding='latin-1', sep=';')
        print(f"File CSV letto con successo. Trovati {len(df)} giocatori.")
    except Exception as e:
        print(f"Errore durante la lettura del CSV: {e}")
        return

    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    print("Pulizia della tabella 'giocatori'...")
    cursor.execute("DELETE FROM giocatori")
    conn.commit()
    
    giocatori_aggiunti = 0
    for index, riga in df.iterrows():
        nome = riga[COLONNA_NOME_GIOCATORE]
        # Standardizziamo il nome della squadra in MAIUSCOLO
        squadra = riga[COLONNA_SQUADRA].upper() 
        
        cursor.execute("INSERT INTO giocatori (nome_giocatore, squadra) VALUES (?, ?)", (nome, squadra))
        giocatori_aggiunti += 1
            
    conn.commit()
    conn.close()
    
    print(f"\n--- Importazione completata! Giocatori aggiunti: {giocatori_aggiunti} ---")

if __name__ == '__main__':
    importa_giocatori_da_csv()