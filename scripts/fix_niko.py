import sqlite3

# Connettiamoci al database
conn = sqlite3.connect('database.db')
cursor = conn.cursor()

ID_DA_TENERE = 5
ID_DA_ELIMINARE = 26

print(f"Inizio il trasferimento dati dall'utente {ID_DA_ELIMINARE} all'utente {ID_DA_TENERE}...")

# 1. Trasferiamo i pronostici delle singole giornate
cursor.execute("SELECT * FROM pronostici_giornata WHERE id_utente = ?", (ID_DA_ELIMINARE,))
pronostici_26 = cursor.fetchall()

for p in pronostici_26:
    id_pronostico = p[0]
    id_partita = p[2]

    # Controlliamo se l'ID 5 ha già giocato questa specifica partita
    cursor.execute("SELECT id FROM pronostici_giornata WHERE id_utente = ? AND id_partita = ?", (ID_DA_TENERE, id_partita))
    esiste_gia = cursor.fetchone()

    if not esiste_gia:
        # Passiamo il pronostico al 5
        cursor.execute("UPDATE pronostici_giornata SET id_utente = ? WHERE id = ?", (ID_DA_TENERE, id_pronostico))
    else:
        # Se il 5 lo aveva già fatto, eliminiamo il doppione del 26
        cursor.execute("DELETE FROM pronostici_giornata WHERE id = ?", (id_pronostico,))

# 2. Controlliamo i pronostici iniziali (vincitore, capocannoniere, ecc.)
cursor.execute("SELECT id FROM pronostici_iniziali WHERE id_utente = ?", (ID_DA_ELIMINARE,))
iniziali_26 = cursor.fetchone()

if iniziali_26:
    cursor.execute("SELECT id FROM pronostici_iniziali WHERE id_utente = ?", (ID_DA_TENERE,))
    iniziali_5 = cursor.fetchone()
    if not iniziali_5:
        # Se il 5 non li aveva fatti, gli passiamo quelli del 26
        cursor.execute("UPDATE pronostici_iniziali SET id_utente = ? WHERE id_utente = ?", (ID_DA_TENERE, ID_DA_ELIMINARE))
    else:
        # Eliminiamo il doppione
        cursor.execute("DELETE FROM pronostici_iniziali WHERE id_utente = ?", (ID_DA_ELIMINARE,))

# 3. Pulizia finale: eliminiamo i vecchi punteggi e l'account 26
cursor.execute("DELETE FROM punteggi WHERE id_utente = ?", (ID_DA_ELIMINARE,))
cursor.execute("DELETE FROM utenti WHERE id = ?", (ID_DA_ELIMINARE,))

conn.commit()
conn.close()

print("Intervento chirurgico completato con successo! Paziente salvato.")