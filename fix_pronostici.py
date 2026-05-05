import sqlite3
import os

# --- CONFIGURAZIONE ---
# Assicurati che il percorso del database sia corretto
# Questo percorso funziona se lo script è nella stessa cartella di app.py
DATABASE_PATH = os.path.join(os.path.dirname(__file__), 'database.db')
# --------------------

def get_db_connection():
    """Crea una connessione al database."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def main():
    """
    Funzione principale per ricollegare i pronostici orfani.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Troviamo tutti i pronostici il cui ID partita non esiste più nella tabella partite
    cursor.execute("""
        SELECT DISTINCT p.giornata, p.squadra_casa, p.squadra_ospite
        FROM pronostici_giornata pg
        JOIN partite p ON pg.id_partita = p.id
        WHERE pg.id_partita NOT IN (SELECT id FROM partite)
    """)
    # Questa query è concettualmente ciò che vogliamo fare, ma è difficile da eseguire
    # direttamente. Un approccio più semplice è lavorare su tutti i pronostici e
    # assicurarci che siano collegati correttamente.

    print("--- Avvio dello script di riparazione pronostici ---")
    
    # Prendiamo tutti i pronostici esistenti
    pronostici_da_verificare = cursor.execute("SELECT id, id_partita, id_utente FROM pronostici_giornata").fetchall()
    
    partite_cache = {}
    pronostici_ricollegati = 0

    for pronostico in pronostici_da_verificare:
        # Per ogni pronostico, troviamo i dettagli della partita a cui è collegato
        # Questo potrebbe essere un ID vecchio o nuovo
        dettagli_partita_originale = cursor.execute(
            "SELECT giornata, squadra_casa, squadra_ospite FROM partite WHERE id = ?", 
            (pronostico['id_partita'],)
        ).fetchone()

        if not dettagli_partita_originale:
            print(f"ATTENZIONE: Trovato pronostico (ID: {pronostico['id']}) per una partita (ID: {pronostico['id_partita']}) che non esiste più. Ignorato.")
            continue

        # Ora, troviamo l'ID CORRENTE di quella stessa partita
        giornata = dettagli_partita_originale['giornata']
        casa = dettagli_partita_originale['squadra_casa']
        ospite = dettagli_partita_originale['squadra_ospite']
        
        # Usiamo una cache per non interrogare il db inutilmente
        if (giornata, casa, ospite) not in partite_cache:
            partita_corrente = cursor.execute(
                "SELECT id FROM partite WHERE giornata = ? AND squadra_casa = ? AND squadra_ospite = ?",
                (giornata, casa, ospite)
            ).fetchone()
            if partita_corrente:
                partite_cache[(giornata, casa, ospite)] = partita_corrente['id']
            else:
                 partite_cache[(giornata, casa, ospite)] = None


        id_partita_corretto = partite_cache.get((giornata, casa, ospite))

        if id_partita_corretto and id_partita_corretto != pronostico['id_partita']:
            print(f"Ricollegamento: Pronostico ID {pronostico['id']} per {casa}-{ospite} (Giornata {giornata})")
            print(f"  -> ID vecchio: {pronostico['id_partita']} -> ID nuovo: {id_partita_corretto}")
            
            # Eseguiamo l'aggiornamento
            cursor.execute("UPDATE pronostici_giornata SET id_partita = ? WHERE id = ?", (id_partita_corretto, pronostico['id']))
            pronostici_ricollegati += 1

    if pronostici_ricollegati > 0:
        print(f"\nOperazione completata. {pronostici_ricollegati} pronostici sono stati ricollegati.")
        print("Applicazione delle modifiche al database...")
        # !!! IMPORTANTE: Questa riga salva le modifiche. !!!
        # Eseguila solo quando sei sicuro.
        conn.commit() 
        print("Modifiche salvate con successo.")
    else:
        print("\nNessun pronostico da ricollegare. Il database sembra già corretto.")


    conn.close()
    print("--- Script terminato ---")


if __name__ == "__main__":
    main()