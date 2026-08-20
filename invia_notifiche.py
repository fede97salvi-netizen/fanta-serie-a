import os
import json
from pywebpush import webpush, WebPushException
from db_utils import db_conn, db_fetchall

def invia_promemoria_generale(titolo, messaggio):
    # Recuperiamo le chiavi di sicurezza nascoste su Render
    chiave_privata = os.environ.get('VAPID_PRIVATE_KEY')
    email = os.environ.get('VAPID_CLAIM_EMAIL')
    
    if not chiave_privata:
        print("Errore: Chiave privata VAPID mancante!")
        return

    # Peschiamo tutti gli iscritti dal database
    with db_conn() as conn:
        utenti_iscritti = db_fetchall(conn, "SELECT subscription_info FROM push_subscriptions")
        
    print(f"Trovati {len(utenti_iscritti)} dispositivi iscritti. Inizio invio...")

    # Inviamo la notifica a ciascuno
    for utente in utenti_iscritti:
        # A seconda di come legge il DB, assicuriamoci che sia un dizionario
        sub_info = utente[0] if isinstance(utente, tuple) else utente['subscription_info']
        if isinstance(sub_info, str):
            sub_info = json.loads(sub_info)
            
        try:
            webpush(
                subscription_info=sub_info,
                data=json.dumps({"title": titolo, "body": messaggio}),
                vapid_private_key=chiave_privata,
                vapid_claims={"sub": email}
            )
        except WebPushException as ex:
            print("Invio fallito per un utente:", repr(ex))
            
    print("Spedizione completata! 🎯")

# --- TESTIAMOLO SUBITO ---
if __name__ == "__main__":
    invia_promemoria_generale(
        "FantaSerieA: Sveglia! ⏰", 
        "Ricordati di inserire i pronostici, la giornata sta per iniziare!"
    )
