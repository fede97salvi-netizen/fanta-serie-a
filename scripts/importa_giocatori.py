import pandas as pd
import sqlite3
import os

DATABASE_FILE = 'database.db'
CSV_FILE = 'Quotazioni_Fantacalcio_Stagione_2026_27.csv'
COLONNA_NOME_GIOCATORE = 'Nome' 
COLONNA_SQUADRA = 'Squadra'

# --- IL NOSTRO "TRADUTTORE" AUTOMATICO ---
# A sinistra il nome usato dal Fantacalcio (CSV)
# A destra il nome ufficiale usato dalla API (football-data.org)
MAPPATURA_SQUADRE = {
    "INTER": "FC INTERNAZIONALE MILANO",
    "JUVENTUS": "JUVENTUS FC",
    "MILAN": "AC MILAN",
    "ROMA": "AS ROMA",
    "NAPOLI": "SSC NAPOLI",
    "LAZIO": "SS LAZIO",
    "FIORENTINA": "ACF FIORENTINA",
    "ATALANTA": "ATALANTA BC",
    "BOLOGNA": "BOLOGNA FC 1909",
    "TORINO": "TORINO FC",
    "GENOA": "GENOA CFC",
    "LECCE": "US LECCE",
    "UDINESE": "UDINESE CALCIO",
    "CAGLIARI": "CAGLIARI CALCIO",
    "MONZA": "AC MONZA",
    "COMO": "COMO 1907",
    "PARMA": "PARMA CALCIO 1913",
    "VENEZIA": "VENEZIA FC",
    "FROSINONE": "FROSINONE CALCIO",
    "SASSUOLO": "US SASSUOLO CALCIO"
}

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
        # Prendiamo il nome dal CSV, rimuoviamo spazi extra e lo facciamo MAIUSCOLO
        squadra_csv = str(riga[COLONNA_SQUADRA]).upper().strip() 
        
        # Traduciamo il nome se esiste nel nostro dizionario, altrimenti teniamo l'originale
        squadra_finale = MAPPATURA_SQUADRE.get(squadra_csv, squadra_csv)
        
        cursor.execute("INSERT INTO giocatori (nome_giocatore, squadra) VALUES (?, ?)", (nome, squadra_finale))
        giocatori_aggiunti += 1
            
    conn.commit()
    conn.close()
    
    print(f"\n--- Importazione completata! Giocatori aggiunti: {giocatori_aggiunti} ---")

if __name__ == '__main__':
    importa_giocatori_da_csv()
