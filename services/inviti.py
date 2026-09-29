"""
Codice d'invito per la registrazione.

La registrazione è possibile solo con il link d'invito che l'admin copia dal
pannello Utenti (/registrazione?invito=<codice>). Rigenerare il codice rende
inutilizzabili i link condivisi in precedenza; gli account già creati non
sono toccati.
"""

import hmac
import logging
import secrets

from db_utils import db_conn, db_execute, db_fetchone, db_commit, row_get

log = logging.getLogger('fanta')

CHIAVE_CODICE_INVITO = 'codice_invito'


def _nuovo_codice() -> str:
    return secrets.token_urlsafe(12)


def leggi_codice_invito() -> str | None:
    with db_conn() as conn:
        row = db_fetchone(conn, 'SELECT valore FROM impostazioni WHERE chiave = ?',
                          (CHIAVE_CODICE_INVITO,))
    return row_get(row, 'valore') if row else None


def rigenera_codice_invito() -> str:
    codice = _nuovo_codice()
    with db_conn() as conn:
        db_execute(
            conn,
            'INSERT INTO impostazioni (chiave, valore) VALUES (?, ?) '
            'ON CONFLICT (chiave) DO UPDATE SET valore = excluded.valore',
            (CHIAVE_CODICE_INVITO, codice),
        )
        db_commit(conn)
    return codice


def assicura_codice_invito() -> None:
    """All'avvio: crea il codice se non esiste ancora."""
    try:
        if not leggi_codice_invito():
            rigenera_codice_invito()
            log.info('Creato il codice d\'invito per la registrazione.')
    except Exception:
        log.exception('Errore inizializzazione codice invito')


def codice_invito_valido(codice: str | None) -> bool:
    atteso = leggi_codice_invito()
    if not codice or not atteso:
        return False
    return hmac.compare_digest(codice.strip(), atteso)
