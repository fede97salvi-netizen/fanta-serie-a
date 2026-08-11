import pandas as pd
import os
import sys

CSV_FILE = 'Quotazioni_Fantacalcio_Stagione_2026_27.csv'
COLONNA_NOME_GIOCATORE = 'Nome' 
COLONNA_SQUADRA = 'Squadra'

# --- IL NOSTRO "TRADUTTORE" AUTOMATICO ---
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

def get_connection():
    """Cerca la connessione a Supabase (PostgreSQL), altrimenti usa SQLite locale."""
    database_url = os.environ.get('DATABASE_URL')
    if database_url:
        import psycopg2
        # SQLAlchemy/Supabase url fix
        url = database_url.replace('postgres://', 'postgresql://', 1)
        return psycopg2.connect(url), True
    else:
        import sqlite3
        return sqlite3.connect('database.db'), False

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

    conn, is_postgres = get_connection()
    cursor = conn.cursor()
    
    print("Pulizia della tabella 'giocatori'...")
    cursor.execute("DELETE FROM giocatori")
    conn.commit()
    
    # Su Postgres il segnaposto per le variabili è %s, su SQLite è ?
    placeholder = "%s" if is_postgres else "?"
    query_inserimento = f"INSERT INTO giocatori (nome_giocatore, squadra) VALUES ({placeholder}, {placeholder})"
    
    giocatori_aggiunti = 0
    for index, riga in df.iterrows():
        nome = riga[COLONNA_NOME_GIOCATORE]
        squadra_csv = str(riga[COLONNA_SQUADRA]).upper().strip() 
        squadra_finale = MAPPATURA_SQUADRE.get(squadra_csv, squadra_csv)
        
        cursor.execute(query_inserimento, (nome, squadra_finale))
        giocatori_aggiunti += 1
            
    conn.commit()
    conn.close()
    
    print(f"\n--- Importazione completata su {'Supabase' if is_postgres else 'Database Locale'}! Giocatori aggiunti: {giocatori_aggiunti} ---")

if __name__ == '__main__':
    importa_giocatori_da_csv()
