"""
Servizio email via Resend API.
Estratto da app.py per separazione delle responsabilità.
"""

import threading
import logging
from datetime import datetime

import pytz
import requests

log = logging.getLogger('fanta')


def _get_email_config():
    """Carica la config email dall'app Flask corrente (evita circular import)."""
    from flask import current_app
    return {
        'api_key':      current_app.config['RESEND_API_KEY'],
        'from_name':    current_app.config['EMAIL_FROM_NAME'],
        'from_address': current_app.config['EMAIL_FROM_ADDRESS'],
        'app_url':      current_app.config['APP_URL'],
    }


def invia_email(destinatari: list[str], oggetto: str, corpo_html: str):
    """Invia email tramite Resend API. Restituisce (n_successi, lista_errori)."""
    cfg = _get_email_config()
    if not cfg['api_key']:
        return 0, ['Email non configurata (RESEND_API_KEY mancante)']

    successi, errori = 0, []
    for dest in destinatari:
        try:
            r = requests.post(
                'https://api.resend.com/emails',
                headers={
                    'Authorization': f"Bearer {cfg['api_key']}",
                    'Content-Type': 'application/json',
                },
                json={
                    'from':    f"{cfg['from_name']} <{cfg['from_address']}>",
                    'to':      [dest],
                    'subject': oggetto,
                    'html':    corpo_html,
                },
                timeout=15,
            )
            if r.status_code in (200, 201):
                successi += 1
                log.info(f'[EMAIL] Inviata a {dest}')
            else:
                try:
                    msg = r.json().get('message', r.text[:100])
                except Exception:
                    msg = r.text[:100]
                errori.append(f'{dest}: {msg}')
                log.warning(f'[EMAIL] Errore per {dest}: {msg}')
        except Exception:
            errori.append(f'{dest}: eccezione di rete')
            log.exception(f'[EMAIL] Eccezione per {dest}')
    return successi, errori


def invia_email_async(destinatari: list[str], oggetto: str, corpo_html: str):
    """Invia email in background senza bloccare la richiesta HTTP."""
    from flask import current_app
    app = current_app._get_current_object()  # riferimento sicuro per il thread

    def _invia():
        with app.app_context():
            try:
                log.info(f'[EMAIL] Avvio invio a {len(destinatari)} destinatari...')
                successi, errori = invia_email(destinatari, oggetto, corpo_html)
                log.info(f'[EMAIL] Completato: {successi} successi, {len(errori)} errori')
                for e in errori:
                    log.warning(f'[EMAIL] Errore: {e}')
            except Exception:
                log.exception('Eccezione nel thread email')

    threading.Thread(target=_invia, daemon=True).start()


def converti_data_email(data_ora_utc_str: str) -> str:
    if not data_ora_utc_str:
        return 'Data da definire'
    try:
        roma_tz = pytz.timezone('Europe/Rome')
        from services.game_logic import parse_flexible_datetime
        orario_naive = parse_flexible_datetime(str(data_ora_utc_str))
        if orario_naive is None:
            return str(data_ora_utc_str)
        return (pytz.utc.localize(orario_naive)
                .astimezone(roma_tz)
                .strftime('%d/%m/%Y alle %H:%M'))
    except Exception:
        log.exception('Errore conversione data email')
        return str(data_ora_utc_str)


def build_email_giornata(giornata: int, partite: list[dict]) -> str:
    """Genera l'HTML per l'email di notifica giornata."""
    from flask import current_app
    app_url = current_app.config.get('APP_URL', '')

    partite_html = ''
    for p in partite:
        data_str = converti_data_email(p.get('data_ora_partita') or '')
        sc = p.get('squadra_casa', '')
        so = p.get('squadra_ospite', '')
        partite_html += f"""
        <tr><td style="padding:12px 16px;border-bottom:1px solid #e5e7eb;">
          <strong style="font-size:16px;color:#1e3a5f;">{sc} vs {so}</strong>
          <div style="font-size:13px;color:#6b7280;margin-top:4px;">📅 {data_str}</div>
        </td></tr>"""

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0"
         style="background:#f3f4f6;padding:32px 16px;">
    <tr><td align="center">
      <table width="100%"
             style="max-width:520px;background:white;border-radius:16px;
                    overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08);">
        <tr><td style="background:linear-gradient(135deg,#003f8a,#0f4a1e);
                        padding:28px 24px;text-align:center;">
          <div style="font-size:32px;margin-bottom:8px;">🏆</div>
          <h1 style="color:white;margin:0;font-size:24px;letter-spacing:1px;">
            FantaSerieA</h1>
          <p style="color:rgba(255,255,255,0.8);margin:8px 0 0;font-size:14px;">
            Giornata {giornata} — Inserisci i tuoi pronostici!</p>
        </td></tr>
        <tr><td style="padding:24px;">
          <p style="color:#374151;font-size:15px;margin:0 0 16px;">
            Le partite della <strong>giornata {giornata}</strong> sono pronte.</p>
          <h2 style="color:#1e3a5f;font-size:16px;margin:0 0 12px;
                     text-transform:uppercase;letter-spacing:1px;">
            Le 3 partite da pronosticare</h2>
          <table width="100%" cellpadding="0" cellspacing="0"
                 style="border:1px solid #e5e7eb;border-radius:10px;overflow:hidden;">
            {partite_html}
          </table>
          <div style="text-align:center;margin-top:24px;">
            <a href="{app_url}/pronostici-giornata/{giornata}"
               style="background:linear-gradient(135deg,#1565c0,#0090d4);color:white;
                      padding:12px 32px;border-radius:8px;text-decoration:none;
                      font-weight:bold;font-size:15px;display:inline-block;">
              Inserisci i pronostici →
            </a>
          </div>
          <p style="color:#9ca3af;font-size:12px;text-align:center;margin-top:24px;">
            Ricevi questa email perché sei iscritto a FantaSerieA.<br>
            <a href="{app_url}" style="color:#0090d4;">Vai all'app</a>
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""
