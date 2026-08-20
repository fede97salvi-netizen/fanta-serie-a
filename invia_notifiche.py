import os
import json
from datetime import datetime, timezone

import pytz
from pywebpush import webpush, WebPushException

from db_utils import db_conn, db_execute, db_fetchall, db_commit, row_get
from services.game_logic import parse_flexible_datetime

# Finestra in cui una partita viene considerata "in arrivo tra mezz'ora":
# larga abbastanza da assorbire i ritardi tipici dello scheduler di GitHub
# Actions (il cron gira ogni 10 minuti ma può slittare).
FINESTRA_MIN_MINUTI = 15
FINESTRA_MAX_MINUTI = 35

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


def _formatta_orario_italia(data_ora_utc_str):
    orario_naive = parse_flexible_datetime(data_ora_utc_str)
    if not orario_naive:
        return ''
    roma_tz = pytz.timezone('Europe/Rome')
    return pytz.utc.localize(orario_naive).astimezone(roma_tz).strftime('%H:%M')


def invia_promemoria_partite():
    """Avvisa gli utenti che non hanno ancora pronosticato una partita che
    inizia tra FINESTRA_MIN_MINUTI e FINESTRA_MAX_MINUTI minuti.

    Idempotente: ogni partita viene processata una sola volta grazie al
    flag partite.promemoria_inviato, così anche se lo scheduler esterno
    gira più volte nella stessa finestra non si inviano doppioni.
    """
    chiave_privata = os.environ.get('VAPID_PRIVATE_KEY')
    email = os.environ.get('VAPID_CLAIM_EMAIL', 'mailto:admin@fantaseriea.com')
    if not chiave_privata:
        return "Errore: VAPID_PRIVATE_KEY mancante nelle variabili di ambiente Render"

    ora_utc = datetime.now(timezone.utc)
    totale_inviate = 0
    totale_errori = 0
    dettagli = []

    with db_conn() as conn:
        candidate = db_fetchall(
            conn,
            "SELECT * FROM partite WHERE pronosticabile = TRUE "
            "AND data_ora_partita IS NOT NULL AND promemoria_inviato = FALSE",
        )

        partite_da_avvisare = []
        for partita in candidate:
            orario_naive = parse_flexible_datetime(row_get(partita, 'data_ora_partita'))
            if not orario_naive:
                continue
            minuti_a_inizio = (
                orario_naive.replace(tzinfo=timezone.utc) - ora_utc
            ).total_seconds() / 60
            if FINESTRA_MIN_MINUTI <= minuti_a_inizio <= FINESTRA_MAX_MINUTI:
                partite_da_avvisare.append(partita)

        for partita in partite_da_avvisare:
            pid = row_get(partita, 'id')
            casa = row_get(partita, 'squadra_casa')
            ospite = row_get(partita, 'squadra_ospite')
            orario_locale = _formatta_orario_italia(row_get(partita, 'data_ora_partita'))

            destinatari = db_fetchall(
                conn,
                "SELECT ps.id_utente, ps.subscription_info FROM push_subscriptions ps "
                "WHERE ps.id_utente IS NOT NULL AND ps.id_utente NOT IN ("
                "  SELECT id_utente FROM pronostici_giornata WHERE id_partita = ?"
                ")",
                (pid,),
            )

            titolo = "⏰ Manca mezz'ora!"
            messaggio = f"{casa} - {ospite} inizia alle {orario_locale}. Non hai ancora pronosticato!"

            for dest in destinatari:
                id_utente = row_get(dest, 'id_utente')
                sub_info = row_get(dest, 'subscription_info')
                if isinstance(sub_info, str):
                    sub_info = json.loads(sub_info)
                try:
                    webpush(
                        subscription_info=sub_info,
                        data=json.dumps({"title": titolo, "body": messaggio}),
                        vapid_private_key=chiave_privata,
                        vapid_claims={"sub": email},
                    )
                    totale_inviate += 1
                except WebPushException as ex:
                    totale_errori += 1
                    status = ex.response.status_code if ex.response is not None else None
                    dettagli.append(f"partita {pid} utente {id_utente}: status={status}")
                    if status in (404, 410):
                        # Subscription non più valida lato browser: la rimuoviamo
                        # per non ritentare inutilmente ad ogni prossimo giro.
                        db_execute(conn, "DELETE FROM push_subscriptions WHERE id_utente = ?",
                                   (id_utente,))

            db_execute(conn, "UPDATE partite SET promemoria_inviato = TRUE WHERE id = ?", (pid,))

        db_commit(conn)

    esito = (f"Partite processate: {len(partite_da_avvisare)}. "
             f"Notifiche inviate: {totale_inviate}. Errori: {totale_errori}.")
    if dettagli:
        esito += " | " + " || ".join(dettagli)
    return esito
