"""
Blueprint admin — pannello di amministrazione.

Tutte le route usano endpoint= espliciti identici alla V2,
cosicché url_for() nei template non richiede modifiche.

Accesso protetto da require_admin() su ogni route.
"""

import logging
import random
import string
import threading
import time

import requests as http_requests
from flask import (
    Blueprint, render_template, request, redirect,
    url_for, session, flash,
)

from db_utils import (
    db_conn, db_execute, db_fetchone, db_fetchall, db_commit,
    row_get, USE_POSTGRES,
)
from services.game_logic import (
    calcola_e_aggiorna_punti_giornata,
    ricalcola_punteggi_totali,
    ricalcola_punteggi_finali,
)
from services.email_service import invia_email_async, build_email_giornata
from blueprints.auth import hash_password

log = logging.getLogger('fanta')

admin_bp = Blueprint('admin', __name__)


def require_admin():
    return 'nome_utente' not in session or not session.get('is_admin')


def _football_api_get(path: str, params: dict = None):
    from flask import current_app
    api_key = current_app.config.get('FOOTBALL_API_KEY', '')
    base    = current_app.config.get('FOOTBALL_API_BASE', '')
    if not api_key:
        return None, 'FOOTBALL_API_KEY non configurata.'
    try:
        r = http_requests.get(
            f'{base}{path}',
            headers={'X-Auth-Token': api_key},
            params=params or {},
            timeout=15,
        )
        if r.status_code != 200:
            return None, f'API risposta {r.status_code}: {r.text[:200]}'
        return r.json(), None
    except Exception as e:
        log.exception('Errore chiamata API football-data')
        return None, str(e)


def _safe_int(value, lo=None, hi=None):
    if value is None or value == '':
        return None
    try:
        v = int(value)
    except (ValueError, TypeError):
        return None
    if lo is not None and v < lo:
        return None
    if hi is not None and v > hi:
        return None
    return v


# ─── Dashboard ────────────────────────────────────────────────────────────────

@admin_bp.route('/admin', endpoint='admin_home')
def admin_home():
    if require_admin():
        return 'Accesso negato.', 403
    with db_conn() as conn:
        ga_row = db_fetchone(
            conn, 'SELECT giornata FROM stato_giornata WHERE is_attiva = TRUE')
        giornata_attiva = row_get(ga_row, 'giornata') if ga_row else None
        partite_attive  = []
        if giornata_attiva:
            partite_attive = db_fetchall(
                conn,
                'SELECT * FROM partite WHERE giornata = ? AND pronosticabile = TRUE',
                (giornata_attiva,),
            )
    return render_template('admin.html', giornata_attiva=giornata_attiva,
                           partite_attive=partite_attive, session=session)


# ─── Utenti ───────────────────────────────────────────────────────────────────

@admin_bp.route('/admin/utenti', endpoint='admin_utenti')
def admin_utenti():
    if require_admin():
        return 'Accesso negato.', 403
    with db_conn() as conn:
        utenti = db_fetchall(
            conn,
            'SELECT id, nome_utente, is_temp_password, is_admin '
            'FROM utenti ORDER BY nome_utente',
        )
    return render_template('admin_utenti.html', utenti=utenti, session=session)


@admin_bp.route('/admin/resetta-password/<int:id_utente>',
                methods=['POST'], endpoint='admin_resetta_password')
def admin_resetta_password(id_utente: int):
    if require_admin():
        return 'Accesso negato.', 403
    pw_temp = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    with db_conn() as conn:
        db_execute(conn,
                   'UPDATE utenti SET password = ?, is_temp_password = TRUE '
                   'WHERE id = ?',
                   (hash_password(pw_temp), id_utente))
        utente = db_fetchone(conn, 'SELECT nome_utente FROM utenti WHERE id = ?',
                             (id_utente,))
        db_commit(conn)
    nome = row_get(utente, 'nome_utente') if utente else 'Utente'
    flash(f'Password temporanea per {nome}: {pw_temp}', 'success')
    return redirect(url_for('admin.admin_utenti'))


@admin_bp.route('/admin/elimina-utente/<int:id_utente>',
                methods=['POST'], endpoint='admin_elimina_utente')
def admin_elimina_utente(id_utente: int):
    if require_admin():
        return 'Accesso negato.', 403
    with db_conn() as conn:
        utente = db_fetchone(
            conn, 'SELECT nome_utente, is_admin FROM utenti WHERE id = ?',
            (id_utente,))
        if not utente:
            flash('Utente non trovato.', 'warning')
            return redirect(url_for('admin.admin_utenti'))
        if row_get(utente, 'is_admin'):
            flash('Non puoi eliminare un admin.', 'warning')
            return redirect(url_for('admin.admin_utenti'))
        for tbl in ('punteggi', 'punteggi_giornata',
                    'pronostici_giornata', 'pronostici_iniziali'):
            db_execute(conn, f'DELETE FROM {tbl} WHERE id_utente = ?',
                       (id_utente,))
        db_execute(conn, 'DELETE FROM utenti WHERE id = ?', (id_utente,))
        db_commit(conn)
    flash(f"Utente {row_get(utente, 'nome_utente')} eliminato.", 'success')
    return redirect(url_for('admin.admin_utenti'))


# ─── Gestione partite ─────────────────────────────────────────────────────────

@admin_bp.route('/admin/gestisci-partite', endpoint='admin_gestisci_partite')
def admin_gestisci_partite():
    if require_admin():
        return 'Accesso negato.', 403
    with db_conn() as conn:
        giornata_sel = request.args.get('giornata', type=int)
        giornate_rows = db_fetchall(
            conn, 'SELECT DISTINCT giornata FROM partite ORDER BY giornata')
        giornate_disponibili = [row_get(r, 'giornata') for r in giornate_rows]
        partite = db_fetchall(
            conn,
            ('SELECT * FROM partite WHERE giornata = ? ORDER BY data_ora_partita'
             if giornata_sel
             else 'SELECT * FROM partite ORDER BY giornata, data_ora_partita'),
            (giornata_sel,) if giornata_sel else (),
        )
        ga_row = db_fetchone(
            conn, 'SELECT giornata FROM stato_giornata WHERE is_attiva = TRUE')
        partite_attive, giocatori_per_partita, giornata_attiva_dict = [], {}, None
        if ga_row:
            g = row_get(ga_row, 'giornata')
            giornata_attiva_dict = {'giornata': g}
            partite_attive = db_fetchall(
                conn,
                'SELECT * FROM partite WHERE giornata = ? AND pronosticabile = TRUE',
                (g,))
            # Carica tutti i giocatori delle squadre attive in UNA sola query (no N+1)
            squadre = set()
            for p in partite_attive:
                squadre.add((row_get(p, 'squadra_casa')   or '').upper())
                squadre.add((row_get(p, 'squadra_ospite') or '').upper())
            if squadre:
                ph = ','.join(['?'] * len(squadre))
                tutti_giocatori = db_fetchall(
                    conn,
                    f'SELECT nome_giocatore, squadra FROM giocatori '
                    f'WHERE UPPER(squadra) IN ({ph}) '
                    f'ORDER BY squadra, nome_giocatore',
                    tuple(squadre),
                )
                # Raggruppa per partita
                giocatori_per_squadra = {}
                for g_row in tutti_giocatori:
                    s = (row_get(g_row, 'squadra') or '').upper()
                    giocatori_per_squadra.setdefault(s, []).append(g_row)
                for p in partite_attive:
                    pid = row_get(p, 'id')
                    sc  = (row_get(p, 'squadra_casa')   or '').upper()
                    so  = (row_get(p, 'squadra_ospite') or '').upper()
                    giocatori_per_partita[pid] = (
                        giocatori_per_squadra.get(sc, []) +
                        giocatori_per_squadra.get(so, [])
                    )
    return render_template('admin_gestisci_partite.html',
                           tutte_le_partite=partite,
                           giornate_disponibili=giornate_disponibili,
                           giornata_selezionata=giornata_sel,
                           giornata_attiva=giornata_attiva_dict,
                           partite_attive=partite_attive,
                           giocatori_per_partita=giocatori_per_partita,
                           session=session)


@admin_bp.route('/admin/aggiungi-partita',
                methods=['POST'], endpoint='aggiungi_partita')
def aggiungi_partita():
    if require_admin():
        return 'Accesso negato.', 403
    giornata = _safe_int(request.form.get('giornata'), lo=1, hi=50)
    if giornata is None:
        flash('Giornata non valida.', 'warning')
        return redirect(url_for('admin.admin_gestisci_partite'))
    with db_conn() as conn:
        db_execute(conn,
                   'INSERT INTO partite (giornata, squadra_casa, squadra_ospite, '
                   'pronosticabile, data_ora_partita) VALUES (?,?,?,?,?)',
                   (giornata,
                    (request.form.get('squadra_casa') or '').upper(),
                    (request.form.get('squadra_ospite') or '').upper(),
                    request.form.get('pronosticabile') == 'on',
                    request.form.get('data_ora_partita')))
        db_commit(conn)
    return redirect(url_for('admin.admin_gestisci_partite'))


@admin_bp.route('/admin/modifica-partita/<int:id_partita>',
                methods=['POST'], endpoint='admin_modifica_partita')
def admin_modifica_partita(id_partita: int):
    if require_admin():
        return 'Accesso negato.', 403
    giornata = _safe_int(request.form.get('giornata'), lo=1, hi=50)
    with db_conn() as conn:
        db_execute(conn,
                   'UPDATE partite SET giornata=?, squadra_casa=?, squadra_ospite=?, '
                   'pronosticabile=?, data_ora_partita=? WHERE id=?',
                   (giornata,
                    (request.form.get('squadra_casa') or '').upper(),
                    (request.form.get('squadra_ospite') or '').upper(),
                    request.form.get('pronosticabile') == 'on',
                    request.form.get('data_ora_partita'),
                    id_partita))
        db_commit(conn)
    return redirect(url_for('admin.admin_gestisci_partite',
                            giornata=request.args.get('giornata')))


@admin_bp.route('/admin/elimina-partita/<int:id_partita>',
                methods=['POST'], endpoint='admin_elimina_partita')
def admin_elimina_partita(id_partita: int):
    if require_admin():
        return 'Accesso negato.', 403
    with db_conn() as conn:
        db_execute(conn, 'DELETE FROM partite WHERE id = ?', (id_partita,))
        db_commit(conn)
    return redirect(url_for('admin.admin_gestisci_partite',
                            giornata=request.args.get('giornata')))


@admin_bp.route('/admin/risultati-giornata/<int:giornata>',
                methods=['POST'], endpoint='admin_risultati_giornata')
def admin_risultati_giornata(giornata: int):
    if require_admin():
        return 'Accesso negato.', 403
    with db_conn() as conn:
        partite = db_fetchall(
            conn,
            'SELECT * FROM partite WHERE giornata = ? AND pronosticabile = TRUE',
            (giornata,),
        )
        for partita in partite:
            pid    = row_get(partita, 'id')
            r_casa = _safe_int(request.form.get(f'risultato_casa_{pid}', '').strip(),
                               lo=0, hi=20)
            r_osp  = _safe_int(request.form.get(f'risultato_ospite_{pid}', '').strip(),
                               lo=0, hi=20)
            marc_lista  = request.form.getlist(f'marcatore_{pid}[]')
            validi      = [m.strip() for m in marc_lista
                           if m.strip() not in ('', 'Nessun marcatore', 'Autogol')]
            if not validi:
                speciali = [m.strip() for m in marc_lista
                            if m.strip() in ('Nessun marcatore', 'Autogol')]
                marc_finale = speciali[0] if speciali else None
            else:
                marc_finale = ', '.join(validi)
            db_execute(conn,
                       'UPDATE partite SET risultato_casa_reale=?, '
                       'risultato_ospite_reale=?, marcatore_reale=? WHERE id=?',
                       (r_casa, r_osp, marc_finale, pid))
        db_commit(conn)
    flash('Risultati salvati con successo!', 'success')
    return redirect(url_for('admin.admin_gestisci_partite'))


@admin_bp.route('/admin/importa-risultati/<int:giornata>',
                methods=['POST'], endpoint='admin_importa_risultati')
def admin_importa_risultati(giornata: int):
    if require_admin():
        return 'Accesso negato.', 403
    try:
        from flask import current_app
        serie_a = current_app.config.get('SERIE_A_CODE', 'SA')
        data, err = _football_api_get(
            f'/competitions/{serie_a}/matches', {'matchday': giornata})
        if err:
            flash(f'Errore API: {err}', 'danger')
            return redirect(url_for('admin.admin_home'))
        risultati_api = [
            {
                'home':          (m['homeTeam']['name'] or '').upper(),
                'away':          (m['awayTeam']['name'] or '').upper(),
                'gol_home':      m['score']['fullTime']['home'],
                'gol_away':      m['score']['fullTime']['away'],
                'marcatori_str': '',
            }
            for m in data.get('matches', [])
            if m.get('status') == 'FINISHED'
        ]
        if not risultati_api:
            flash(f'Nessuna partita terminata per G{giornata}.', 'warning')
            return redirect(url_for('admin.admin_home'))
        with db_conn() as conn:
            partite_db  = db_fetchall(
                conn, 'SELECT * FROM partite WHERE giornata = ?', (giornata,))
            aggiornate  = 0
            non_trovate = []
            for partita in partite_db:
                sc  = (row_get(partita, 'squadra_casa')   or '').upper()
                so  = (row_get(partita, 'squadra_ospite') or '').upper()
                m   = next((r for r in risultati_api
                             if (sc in r['home'] or r['home'] in sc)
                             and (so in r['away'] or r['away'] in so)), None)
                if m:
                    db_execute(conn,
                               'UPDATE partite SET risultato_casa_reale=?, '
                               'risultato_ospite_reale=?, marcatore_reale=? '
                               'WHERE id=?',
                               (m['gol_home'], m['gol_away'],
                                m['marcatori_str'], row_get(partita, 'id')))
                    aggiornate += 1
                else:
                    non_trovate.append(f'{sc} vs {so}')
            db_commit(conn)
        msg = f'Risultati importati: {aggiornate} partite aggiornate.'
        if non_trovate:
            msg += f" Non trovate: {', '.join(non_trovate)} — aggiorna manualmente."
        flash(msg, 'success' if not non_trovate else 'warning')
    except Exception as e:
        log.exception('Errore importazione risultati')
        flash(f"Errore durante l'importazione: {e}", 'danger')
    return redirect(url_for('admin.admin_home'))


@admin_bp.route('/admin/invia-reminder/<int:giornata>',
                methods=['POST'], endpoint='admin_invia_reminder')
def admin_invia_reminder(giornata: int):
    if require_admin():
        return 'Accesso negato.', 403
    try:
        with db_conn() as conn:
            partite = db_fetchall(
                conn,
                'SELECT squadra_casa, squadra_ospite, data_ora_partita '
                'FROM partite WHERE giornata = ? AND pronosticabile = TRUE',
                (giornata,),
            )
            utenti_email = db_fetchall(
                conn,
                "SELECT email FROM utenti WHERE email IS NOT NULL AND email != ''",
            )
        destinatari  = [row_get(u, 'email') for u in utenti_email
                        if row_get(u, 'email')]
        if not destinatari:
            flash('Nessun utente con email registrata.', 'warning')
            return redirect(url_for('admin.admin_home'))
        partite_list = [
            {'squadra_casa':    row_get(p, 'squadra_casa'),
             'squadra_ospite':  row_get(p, 'squadra_ospite'),
             'data_ora_partita': row_get(p, 'data_ora_partita')}
            for p in partite
        ]
        invia_email_async(
            destinatari,
            f'⚽ FantaSerieA — Reminder Giornata {giornata}: inserisci i pronostici!',
            build_email_giornata(giornata, partite_list),
        )
        flash(f'Reminder in invio a {len(destinatari)} utenti!', 'success')
    except Exception as e:
        log.exception('Errore invio reminder')
        flash(f'Errore invio reminder: {e}', 'danger')
    return redirect(url_for('admin.admin_home'))


@admin_bp.route('/admin/aggiorna-risultati-massivo',
                methods=['POST'], endpoint='admin_aggiorna_risultati_massivo')
def admin_aggiorna_risultati_massivo():
    if require_admin():
        return 'Accesso negato.', 403

    from flask import current_app
    app = current_app._get_current_object()

    def _esegui():
        with app.app_context():
            log.info('[MASSIVO] Avvio aggiornamento storico risultati...')
            try:
                serie_a = app.config.get('SERIE_A_CODE', 'SA')
                with db_conn() as conn:
                    giornate = db_fetchall(
                        conn,
                        'SELECT giornata FROM stato_giornata '
                        'WHERE is_in_archivio = TRUE ORDER BY giornata',
                    )
                    for i, g_row in enumerate(giornate):
                        g = row_get(g_row, 'giornata')
                        if i > 0 and i % 9 == 0:
                            log.info(f'[MASSIVO] Pausa rate limit...')
                            time.sleep(62)
                        try:
                            data, err = _football_api_get(
                                f'/competitions/{serie_a}/matches',
                                {'matchday': g},
                            )
                            if err or not data:
                                log.info(f'[MASSIVO] G{g} saltata: {err}')
                                continue
                            partite_db = db_fetchall(
                                conn,
                                'SELECT * FROM partite WHERE giornata = ?', (g,))
                            for partita in partite_db:
                                sc = (row_get(partita, 'squadra_casa')   or '').upper()
                                so = (row_get(partita, 'squadra_ospite') or '').upper()
                                m  = next(
                                    (r for r in data.get('matches', [])
                                     if (r.get('status') == 'FINISHED'
                                         and (sc in (r['homeTeam']['name'] or '').upper()
                                              or (r['homeTeam']['name'] or '').upper() in sc)
                                         and (so in (r['awayTeam']['name'] or '').upper()
                                              or (r['awayTeam']['name'] or '').upper() in so))),
                                    None,
                                )
                                if m:
                                    db_execute(conn,
                                               'UPDATE partite '
                                               'SET risultato_casa_reale=?, '
                                               'risultato_ospite_reale=? '
                                               'WHERE id=?',
                                               (m['score']['fullTime']['home'],
                                                m['score']['fullTime']['away'],
                                                row_get(partita, 'id')))
                            db_commit(conn)
                            log.info(f'[MASSIVO] G{g} completata ({i+1}/{len(giornate)})')
                            time.sleep(7)
                        except Exception:
                            log.exception(f'[MASSIVO] Errore G{g}')
                log.info('[MASSIVO] Aggiornamento completato.')
            except Exception:
                log.exception('[MASSIVO] Errore generale')

    threading.Thread(target=_esegui, daemon=True).start()
    flash('Aggiornamento storico avviato in background (~4 min). '
          'Controlla i log per il progresso.', 'info')
    return redirect(url_for('admin.admin_gestisci_partite'))


@admin_bp.route('/admin/importa-giornata', methods=['GET', 'POST'],
                endpoint='admin_importa_giornata')
def admin_importa_giornata():
    if require_admin():
        return 'Accesso negato.', 403
    partite_da_importare = []
    giornata_selezionata = None
    if request.method == 'POST':
        giornata_selezionata = _safe_int(request.form.get('giornata'), lo=1, hi=50)
        if giornata_selezionata is None:
            flash('Giornata non valida.', 'warning')
            return redirect(url_for('admin.admin_importa_giornata'))
        from flask import current_app
        serie_a = current_app.config.get('SERIE_A_CODE', 'SA')
        data, err = _football_api_get(
            f'/competitions/{serie_a}/matches',
            {'matchday': giornata_selezionata},
        )
        if err:
            flash(f'Errore API: {err}', 'danger')
            return redirect(url_for('admin.admin_importa_giornata'))
        for m in (data or {}).get('matches', []):
            partite_da_importare.append({
                'squadra_casa':   (m['homeTeam']['name'] or '').upper(),
                'squadra_ospite': (m['awayTeam']['name'] or '').upper(),
                'data_ora':       m.get('utcDate', ''),
            })

        if request.form.get('conferma') == '1':
            psel_idx = request.form.getlist('seleziona[]')
            partite_sel = []
            for idx in psel_idx:
                try:
                    partite_sel.append(partite_da_importare[int(idx)])
                except (ValueError, IndexError):
                    continue
            if partite_sel:
                with db_conn() as conn:
                    for p in partite_sel:
                        db_execute(conn,
                                   'INSERT INTO partite (giornata, squadra_casa, '
                                   'squadra_ospite, pronosticabile, data_ora_partita) '
                                   'VALUES (?, ?, ?, TRUE, ?)',
                                   (giornata_selezionata,
                                    p['squadra_casa'], p['squadra_ospite'],
                                    p['data_ora']))
                    if USE_POSTGRES:
                        db_execute(conn,
                                   'INSERT INTO stato_giornata (giornata, is_attiva) '
                                   'VALUES (?, TRUE) ON CONFLICT (giornata) '
                                   'DO UPDATE SET is_attiva = TRUE',
                                   (giornata_selezionata,))
                        db_execute(conn,
                                   'UPDATE stato_giornata SET is_attiva = FALSE '
                                   'WHERE giornata != ?', (giornata_selezionata,))
                    else:
                        db_execute(conn,
                                   'INSERT OR IGNORE INTO stato_giornata '
                                   '(giornata, is_attiva) VALUES (?, 1)',
                                   (giornata_selezionata,))
                        db_execute(conn,
                                   'UPDATE stato_giornata SET is_attiva = 1 '
                                   'WHERE giornata = ?', (giornata_selezionata,))
                        db_execute(conn,
                                   'UPDATE stato_giornata SET is_attiva = 0 '
                                   'WHERE giornata != ?', (giornata_selezionata,))
                    db_commit(conn)

                if request.form.get('invia_email') == 'on':
                    with db_conn() as conn:
                        utenti_email = db_fetchall(
                            conn,
                            "SELECT email FROM utenti "
                            "WHERE email IS NOT NULL AND email != ''",
                        )
                    destinatari = [row_get(u, 'email') for u in utenti_email
                                   if row_get(u, 'email')]
                    if destinatari:
                        invia_email_async(
                            destinatari,
                            f'⚽ FantaSerieA — Giornata {giornata_selezionata} disponibile!',
                            build_email_giornata(giornata_selezionata, partite_sel),
                        )

                session['flash_message'] = (
                    f'Giornata {giornata_selezionata}: '
                    f'{len(partite_sel)} partite importate.')
                return redirect(url_for('admin.admin_home'))

    return render_template('admin_importa_giornata.html',
                           partite=partite_da_importare,
                           giornata_selezionata=giornata_selezionata,
                           session=session)


@admin_bp.route('/admin/email-utenti', methods=['GET', 'POST'],
                endpoint='admin_email_utenti')
def admin_email_utenti():
    if require_admin():
        return 'Accesso negato.', 403
    from services.game_logic import EMAIL_RE
    with db_conn() as conn:
        if request.method == 'POST':
            for utente in db_fetchall(conn, 'SELECT id FROM utenti'):
                uid   = row_get(utente, 'id')
                email = (request.form.get(f'email_{uid}', '') or '').strip()
                if email and EMAIL_RE.match(email):
                    db_execute(conn,
                               'UPDATE utenti SET email = ? WHERE id = ?',
                               (email, uid))
            db_commit(conn)
            flash('Email utenti aggiornate con successo!', 'success')
            return redirect(url_for('admin.admin_email_utenti'))
        utenti = db_fetchall(
            conn,
            'SELECT id, nome_utente, email FROM utenti ORDER BY nome_utente',
        )
    return render_template('admin_email_utenti.html',
                           utenti=utenti, session=session)


@admin_bp.route('/admin/gestisci-email', methods=['GET', 'POST'],
                endpoint='admin_gestisci_email')
def admin_gestisci_email():
    if require_admin():
        return 'Accesso negato.', 403
    from services.game_logic import EMAIL_RE
    with db_conn() as conn:
        if request.method == 'POST':
            aggiornati = 0
            for utente in db_fetchall(conn, 'SELECT id FROM utenti'):
                uid   = row_get(utente, 'id')
                email = (request.form.get(f'email_{uid}', '') or '').strip().lower()
                if email and EMAIL_RE.match(email):
                    db_execute(conn,
                               'UPDATE utenti SET email = ? WHERE id = ?',
                               (email, uid))
                    aggiornati += 1
            db_commit(conn)
            flash(f'Email aggiornate per {aggiornati} utenti.', 'success')
            return redirect(url_for('admin.admin_gestisci_email'))
        utenti = db_fetchall(
            conn,
            'SELECT id, nome_utente, email FROM utenti ORDER BY nome_utente',
        )
    return render_template('admin_gestisci_email.html',
                           utenti=utenti, session=session)


@admin_bp.route('/admin/archivia-giornata/<int:giornata>',
                methods=['POST'], endpoint='archivia_giornata')
def archivia_giornata(giornata: int):
    if require_admin():
        return 'Accesso negato.', 403
    with db_conn() as conn:
        db_execute(conn,
                   'UPDATE stato_giornata '
                   'SET is_attiva = FALSE, is_in_archivio = TRUE '
                   'WHERE giornata = ?', (giornata,))
        prossima = giornata + 1
        if USE_POSTGRES:
            db_execute(conn,
                       'INSERT INTO stato_giornata (giornata, is_attiva) '
                       'VALUES (?, TRUE) ON CONFLICT (giornata) '
                       'DO UPDATE SET is_attiva = TRUE',
                       (prossima,))
        else:
            db_execute(conn,
                       'INSERT OR IGNORE INTO stato_giornata (giornata, is_attiva) '
                       'VALUES (?, 1)', (prossima,))
            db_execute(conn,
                       'UPDATE stato_giornata SET is_attiva = 1 WHERE giornata = ?',
                       (prossima,))
        db_commit(conn)
    return redirect(url_for('admin.admin_home'))


@admin_bp.route('/admin/calcola-punti-giornata/<int:giornata>',
                methods=['POST'], endpoint='admin_calcola_punti_giornata')
def admin_calcola_punti_giornata(giornata: int):
    if require_admin():
        return 'Accesso negato.', 403
    flash(calcola_e_aggiorna_punti_giornata(giornata), 'success')
    return redirect(url_for('admin.admin_home'))


@admin_bp.route('/calcola-punteggi', methods=['POST'],
                endpoint='calcola_punteggi')
def calcola_punteggi():
    if require_admin():
        return 'Accesso negato.', 403
    flash(ricalcola_punteggi_totali(), 'success')
    return redirect(url_for('admin.admin_home'))


@admin_bp.route('/admin/gestisci-pronostici/<int:giornata>',
                methods=['GET', 'POST'], endpoint='admin_gestisci_pronostici')
def admin_gestisci_pronostici(giornata: int):
    if require_admin():
        return 'Accesso negato.', 403
    with db_conn() as conn:
        if request.method == 'POST':
            action = request.form.get('action')
            if action == 'modifica':
                pid = request.form.get('id_pronostico')
                db_execute(conn,
                           'UPDATE pronostici_giornata '
                           'SET esito_pronosticato=?, '
                           'risultato_casa_pronosticato=?, '
                           'risultato_ospite_pronosticato=?, '
                           'marcatore_pronosticato=? '
                           'WHERE id=?',
                           (request.form.get('esito'),
                            _safe_int(request.form.get('risultato_casa'),
                                      lo=0, hi=20),
                            _safe_int(request.form.get('risultato_ospite'),
                                      lo=0, hi=20),
                            request.form.get('marcatore'), pid))
                db_commit(conn)
                return redirect(url_for('admin.admin_gestisci_pronostici',
                                        giornata=giornata))
            elif action == 'cancella':
                pid = request.form.get('id_pronostico')
                db_execute(conn,
                           'DELETE FROM pronostici_giornata WHERE id = ?',
                           (pid,))
                db_commit(conn)
                return redirect(url_for('admin.admin_gestisci_pronostici',
                                        giornata=giornata))
        partite = db_fetchall(
            conn,
            'SELECT * FROM partite WHERE giornata = ? AND pronosticabile = TRUE',
            (giornata,),
        )
        pids = [row_get(p, 'id') for p in partite]
        pronostici_per_partita = {row_get(p, 'id'): [] for p in partite}
        if pids:
            # Carica tutti i pronostici della giornata in UNA query (no N+1)
            ph = ','.join(['?'] * len(pids))
            rows = db_fetchall(
                conn,
                f'SELECT u.nome_utente, pg.* FROM pronostici_giornata pg '
                f'JOIN utenti u ON pg.id_utente = u.id '
                f'WHERE pg.id_partita IN ({ph})',
                tuple(pids),
            )
            for r in rows:
                pid = row_get(r, 'id_partita')
                pronostici_per_partita.setdefault(pid, []).append(r)
    return render_template('admin_gestisci_pronostici.html',
                           giornata=giornata, partite=partite,
                           pronostici_per_partita=pronostici_per_partita,
                           session=session)


@admin_bp.route('/admin/gestisci-pronostici-iniziali',
                endpoint='admin_gestisci_pronostici_iniziali')
def admin_gestisci_pronostici_iniziali():
    if require_admin():
        return 'Accesso negato.', 403
    with db_conn() as conn:
        pronostici = db_fetchall(
            conn,
            'SELECT u.nome_utente, pi.* FROM utenti u '
            'JOIN pronostici_iniziali pi ON u.id = pi.id_utente',
        )
        lock_row  = db_fetchone(
            conn, 'SELECT is_locked FROM stato_pronostici_iniziali WHERE id = 1')
        is_locked = row_get(lock_row, 'is_locked') if lock_row else False
    return render_template('admin_gestisci_pronostici_iniziali.html',
                           pronostici=pronostici, is_locked=is_locked,
                           session=session)


@admin_bp.route('/admin/elimina-pronostico-iniziale/<int:id_pronostico>',
                methods=['POST'], endpoint='admin_elimina_pronostico_iniziale')
def admin_elimina_pronostico_iniziale(id_pronostico: int):
    if require_admin():
        return 'Accesso negato.', 403
    with db_conn() as conn:
        db_execute(conn,
                   'DELETE FROM pronostici_iniziali WHERE id = ?',
                   (id_pronostico,))
        db_commit(conn)
    return redirect(url_for('admin.admin_gestisci_pronostici_iniziali'))


@admin_bp.route('/admin/gestisci-finalizzazione',
                endpoint='admin_gestisci_finalizzazione')
def admin_gestisci_finalizzazione():
    if require_admin():
        return 'Accesso negato.', 403
    with db_conn() as conn:
        lock_row  = db_fetchone(
            conn, 'SELECT is_locked FROM stato_pronostici_iniziali WHERE id = 1')
        is_locked = row_get(lock_row, 'is_locked') if lock_row else False
    return render_template('admin_finalizzazione.html',
                           is_locked=is_locked, session=session)


@admin_bp.route('/admin/blocca-pronostici-iniziali',
                methods=['POST'], endpoint='blocca_pronostici_iniziali')
def blocca_pronostici_iniziali():
    if require_admin():
        return 'Accesso negato.', 403
    with db_conn() as conn:
        db_execute(conn,
                   'UPDATE stato_pronostici_iniziali SET is_locked = TRUE WHERE id = 1')
        db_commit(conn)
    return redirect(url_for('admin.admin_gestisci_finalizzazione'))


@admin_bp.route('/admin/sblocca-pronostici-iniziali',
                methods=['POST'], endpoint='sblocca_pronostici_iniziali')
def sblocca_pronostici_iniziali():
    if require_admin():
        return 'Accesso negato.', 403
    with db_conn() as conn:
        db_execute(conn,
                   'UPDATE stato_pronostici_iniziali SET is_locked = FALSE WHERE id = 1')
        db_commit(conn)
    return redirect(url_for('admin.admin_gestisci_finalizzazione'))


@admin_bp.route('/admin/calcola-punti-finali', methods=['GET', 'POST'],
                endpoint='admin_calcola_punti_finali')
def admin_calcola_punti_finali():
    if require_admin():
        return 'Accesso negato.', 403
    messaggio = None
    with db_conn() as conn:
        if request.method == 'POST':
            db_execute(conn,
                       'UPDATE risultati_finali '
                       'SET squadra_1=?, squadra_2=?, squadra_3=?, '
                       'squadra_4=?, capocannoniere=? WHERE id=1',
                       (request.form.get('squadra_1'),
                        request.form.get('squadra_2'),
                        request.form.get('squadra_3'),
                        request.form.get('squadra_4'),
                        request.form.get('capocannoniere')))
            db_commit(conn)
            messaggio = ricalcola_punteggi_finali()
        rf = db_fetchone(conn, 'SELECT * FROM risultati_finali WHERE id = 1')
    return render_template('admin_calcola_punti_finali.html',
                           risultati_finali=rf, messaggio=messaggio,
                           session=session)


@admin_bp.route('/admin/modifica-giornata-archiviata/<int:giornata>',
                methods=['GET', 'POST'],
                endpoint='admin_modifica_giornata_archiviata')
def admin_modifica_giornata_archiviata(giornata: int):
    if require_admin():
        return 'Accesso negato.', 403
    with db_conn() as conn:
        if request.method == 'POST':
            partite = db_fetchall(
                conn,
                'SELECT * FROM partite WHERE giornata = ? AND pronosticabile = TRUE',
                (giornata,),
            )
            for partita in partite:
                pid    = row_get(partita, 'id')
                r_casa = _safe_int(
                    request.form.get(f'risultato_casa_{pid}', '').strip(),
                    lo=0, hi=20)
                r_osp  = _safe_int(
                    request.form.get(f'risultato_ospite_{pid}', '').strip(),
                    lo=0, hi=20)
                marc   = (request.form.get(f'marcatore_{pid}', '') or '').strip() or None
                db_execute(conn,
                           'UPDATE partite SET risultato_casa_reale=?, '
                           'risultato_ospite_reale=?, marcatore_reale=? '
                           'WHERE id=?',
                           (r_casa, r_osp, marc, pid))
            db_commit(conn)
            flash(f'Risultati giornata {giornata} aggiornati.', 'success')
            return redirect(url_for('admin.admin_modifica_giornata_archiviata',
                                    giornata=giornata))
        partite = db_fetchall(
            conn,
            'SELECT * FROM partite WHERE giornata = ? AND pronosticabile = TRUE '
            'ORDER BY data_ora_partita',
            (giornata,),
        )
        giocatori_per_partita = {}
        squadre = set()
        for p in partite:
            squadre.add((row_get(p, 'squadra_casa')   or '').upper())
            squadre.add((row_get(p, 'squadra_ospite') or '').upper())
        if squadre:
            ph = ','.join(['?'] * len(squadre))
            tutti = db_fetchall(
                conn,
                f'SELECT nome_giocatore, squadra FROM giocatori '
                f'WHERE UPPER(squadra) IN ({ph}) '
                f'ORDER BY squadra, nome_giocatore',
                tuple(squadre),
            )
            per_squadra = {}
            for g in tutti:
                s = (row_get(g, 'squadra') or '').upper()
                per_squadra.setdefault(s, []).append(g)
            for p in partite:
                pid = row_get(p, 'id')
                sc  = (row_get(p, 'squadra_casa')   or '').upper()
                so  = (row_get(p, 'squadra_ospite') or '').upper()
                giocatori_per_partita[pid] = (
                    per_squadra.get(sc, []) + per_squadra.get(so, [])
                )
    return render_template('admin_modifica_giornata_archiviata.html',
                           giornata=giornata, partite=partite,
                           giocatori_per_partita=giocatori_per_partita,
                           session=session)


@admin_bp.route('/admin/ricalcola-tutta-la-classifica',
                methods=['POST'], endpoint='admin_ricalcola_tutta_la_classifica')
def admin_ricalcola_tutta_la_classifica():
    if require_admin():
        return 'Accesso negato.', 403
    flash(ricalcola_punteggi_totali(), 'success')
    return redirect(url_for('admin.admin_home'))
