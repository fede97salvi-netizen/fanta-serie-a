import os
import json
from pywebpush import webpush, WebPushException
from db_utils import db_conn, db_fetchall

def invia_promemoria_generale(titolo, messaggio):
    chiave_privata = os.environ.get('VAPID_PRIVATE_KEY')
    email = os.environ.get('VAPID_CLAIM_EMAIL', 'mailto:admin@fantaseriea.com')
    
    if not chiave_privata:
        print("Errore: VAPID_PRIVATE_KEY mancante!")
        return "Errore: VAPID_PRIVATE_KEY mancante nelle variabili di ambiente Render"

    with db_conn() as conn:
        utenti_iscritti = db_fetchall(conn, "SELECT subscription_info FROM push_subscriptions")
        
    print(f"Trovati {len(utenti_iscritti)} dispositivi iscritti. Inizio invio...")

    inviati = 0
    errori = []
    for utente in utenti_iscritti:
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
            inviati += 1
        except WebPushException as ex:
            dettaglio = str(ex)
            if ex.response is not None:
                dettaglio += f" | status={ex.response.status_code} body={ex.response.text}"
            print("Invio fallito:", dettaglio)
            errori.append(dettaglio)

    esito = f"Notifiche inviate a {inviati} dispositivi!"
    if errori:
        esito += " Errori: " + " || ".join(errori)
    return esito
