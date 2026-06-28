"""Blueprint admin — Fanta Mondiali 2026."""

import logging, random, string, threading, time
import requests as http_requests
from flask import Blueprint, render_template, request, redirect, url_for, session, flash

from db_utils import db_conn, db_execute, db_fetchone, db_fetchall, db_commit, row_get, USE_POSTGRES
from services.game_logic import (
    calcola_e_aggiorna_punti_giornata, calcola_e_aggiorna_punti_fase,
    calcola_e_aggiorna_punti_torneo, ricalcola_tutto,
    _safe_int, FASI_NOMI, FASI_ORDINE
)
from blueprints.auth import hash_password

log = logging.getLogger('mondiali')
admin_bp = Blueprint('admin', __name__)


def require_admin():
    return 'nome_utente' not in session or not session.get('is_admin')


def _api_get(path, params=None):
    from flask import current_app
    key  = current_app.config.get('FOOTBALL_API_KEY', '')
    base = current_app.config.get('FOOTBALL_API_BASE', '')
    code = current_app.config.get('COMPETITION_CODE', 'WC')
    if not key:
        return None, 'FOOTBALL_API_KEY non configurata.'
    try:
        r = http_requests.get(f'{base}{path}',
                              headers={'X-Auth-Token': key},
                              params=params or {}, timeout=15)
        if r.status_code != 200:
            return None, f'API {r.status_code}: {r.text[:200]}'
        return r.json(), None
    except Exception as e:
        log.exception('Errore API')
        return None, str(e)


# ─── Dashboard ────────────────────────────────────────────────────────────────

@admin_bp.route('/admin', endpoint='admin_home')
def admin_home():
    if require_admin():
        return 'Accesso negato.', 403
    with db_conn() as conn:
        g_row = db_fetchone(conn, 'SELECT giornata FROM stato_giornata WHERE is_attiva=TRUE')
        giornata_attiva = row_get(g_row, 'giornata') if g_row else None
        f_row = db_fetchone(conn, 'SELECT * FROM stato_fase WHERE is_attiva=TRUE')
        fase_attiva = row_get(f_row, 'fase') if f_row else None
        stati_fasi = {row_get(r, 'fase'): r for r in db_fetchall(conn, 'SELECT * FROM stato_fase')}
        lock_torneo = db_fetchone(conn, 'SELECT is_locked FROM stato_pronostici_torneo WHERE id=1')
        torneo_locked = row_get(lock_torneo, 'is_locked') if lock_torneo else False
    return render_template('admin.html',
                           giornata_attiva=giornata_attiva,
                           fase_attiva=fase_attiva,
                           stati_fasi=stati_fasi,
                           FASI_NOMI=FASI_NOMI, FASI_ORDINE=FASI_ORDINE,
                           torneo_locked=torneo_locked, session=session)


# ─── Utenti ───────────────────────────────────────────────────────────────────

@admin_bp.route('/admin/utenti', endpoint='admin_utenti')
def admin_utenti():
    if require_admin(): return 'Accesso negato.', 403
    with db_conn() as conn:
        utenti = db_fetchall(conn,
            'SELECT id, nome_utente, is_temp_password, is_admin FROM utenti ORDER BY nome_utente')
    return render_template('admin_utenti.html', utenti=utenti, session=session)


@admin_bp.route('/admin/resetta-password/<int:id_utente>', methods=['POST'],
                endpoint='admin_resetta_password')
def admin_resetta_password(id_utente):
    if require_admin(): return 'Accesso negato.', 403
    pw = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    with db_conn() as conn:
        db_execute(conn,
                   'UPDATE utenti SET password=?, is_temp_password=TRUE WHERE id=?',
                   (hash_password(pw), id_utente))
        u = db_fetchone(conn, 'SELECT nome_utente FROM utenti WHERE id=?', (id_utente,))
        db_commit(conn)
    flash(f"Password temporanea per {row_get(u, 'nome_utente')}: {pw}", 'success')
    return redirect(url_for('admin.admin_utenti'))


@admin_bp.route('/admin/elimina-utente/<int:id_utente>', methods=['POST'],
                endpoint='admin_elimina_utente')
def admin_elimina_utente(id_utente):
    if require_admin(): return 'Accesso negato.', 403
    with db_conn() as conn:
        u = db_fetchone(conn, 'SELECT nome_utente, is_admin FROM utenti WHERE id=?', (id_utente,))
        if row_get(u, 'is_admin'):
            flash('Non puoi eliminare un admin.', 'warning')
            return redirect(url_for('admin.admin_utenti'))
        for tbl in ['punteggi', 'punteggi_giornata', 'punteggi_fase',
                    'pronostici_giornata', 'pronostici_eliminazione', 'pronostici_torneo']:
            db_execute(conn, f'DELETE FROM {tbl} WHERE id_utente=?', (id_utente,))
        db_execute(conn, 'DELETE FROM utenti WHERE id=?', (id_utente,))
        db_commit(conn)
    flash(f"Utente {row_get(u, 'nome_utente')} eliminato.", 'success')
    return redirect(url_for('admin.admin_utenti'))


@admin_bp.route('/admin/gestisci-email', methods=['GET', 'POST'],
                endpoint='admin_gestisci_email')
def admin_gestisci_email():
    if require_admin(): return 'Accesso negato.', 403
    with db_conn() as conn:
        if request.method == 'POST':
            for u in db_fetchall(conn, 'SELECT id FROM utenti'):
                uid   = row_get(u, 'id')
                email = (request.form.get(f'email_{uid}') or '').strip().lower()
                if email:
                    db_execute(conn, 'UPDATE utenti SET email=? WHERE id=?', (email, uid))
            db_commit(conn)
            flash('Email aggiornate.', 'success')
            return redirect(url_for('admin.admin_gestisci_email'))
        utenti = db_fetchall(conn, 'SELECT id, nome_utente, email FROM utenti ORDER BY nome_utente')
    return render_template('admin_gestisci_email.html', utenti=utenti, session=session)


# ─── Gestione gironi ──────────────────────────────────────────────────────────

@admin_bp.route('/admin/gestisci-partite', endpoint='admin_gestisci_partite')
def admin_gestisci_partite():
    if require_admin(): return 'Accesso negato.', 403
    with db_conn() as conn:
        giornata_sel = request.args.get('giornata', type=int)
        girone_sel   = request.args.get('girone', '')
        partite = db_fetchall(conn,
            ('SELECT * FROM partite WHERE giornata=? ORDER BY data_ora_partita'
             if giornata_sel
             else 'SELECT * FROM partite WHERE fase=\'gironi\' ORDER BY girone, giornata, data_ora_partita'),
            (giornata_sel,) if giornata_sel else ())
        giornata_attiva_row = db_fetchone(conn,
            'SELECT giornata FROM stato_giornata WHERE is_attiva=TRUE')
        giornata_attiva = row_get(giornata_attiva_row, 'giornata') if giornata_attiva_row else None
        partite_attive  = []
        if giornata_attiva:
            partite_attive = db_fetchall(conn,
                'SELECT * FROM partite WHERE giornata=? AND pronosticabile=TRUE',
                (giornata_attiva,))

        # --- Aggiunta: Caricamento giocatori per l'autocompletamento ---
        tutti_giocatori = db_fetchall(conn,
            'SELECT nome_giocatore, squadra FROM giocatori ORDER BY squadra, nome_giocatore')
        per_sq_db = {}
        for g in tutti_giocatori:
            s = (row_get(g, 'squadra') or '').upper().strip()
            per_sq_db.setdefault(s, []).append(g)

        giocatori_per_partita = {}
        for p in partite:
            pid = row_get(p, 'id')
            sc  = (row_get(p, 'squadra_casa')   or '').upper().strip()
            so  = (row_get(p, 'squadra_ospite') or '').upper().strip()
            lista = per_sq_db.get(sc, []) + per_sq_db.get(so, [])
            if not lista:
                pref_sc, pref_so = sc[:4], so[:4]
                lista = [g for s_key, gs in per_sq_db.items()
                         for g in gs
                         if (pref_sc and s_key.startswith(pref_sc)) or
                            (pref_so and s_key.startswith(pref_so))]
            giocatori_per_partita[pid] = lista
        # ---------------------------------------------------------------

    return render_template('admin_gestisci_partite.html',
                           partite=partite, giornata_sel=giornata_sel,
                           giornata_attiva=giornata_attiva,
                           partite_attive=partite_attive,
                           giocatori_per_partita=giocatori_per_partita,
                           session=session)


@admin_bp.route('/admin/importa-giornata', methods=['GET', 'POST'],
                endpoint='admin_importa_giornata')
def admin_importa_giornata():
    if require_admin(): return 'Accesso negato.', 403
    from flask import current_app
    partite_api = []
    giornata_sel = None
    if request.method == 'POST':
        giornata_sel = _safe_int(request.form.get('giornata'), lo=1, hi=3)
        if giornata_sel is None:
            flash('Giornata 1-3 nei gironi.', 'warning')
            return redirect(url_for('admin.admin_importa_giornata'))
        code = current_app.config.get('COMPETITION_CODE', 'WC')
        data, err = _api_get(f'/competitions/{code}/matches',
                             {'matchday': giornata_sel})
        if err:
            flash(f'Errore API: {err}', 'danger')
        else:
            for m in (data or {}).get('matches', []):
                partite_api.append({
                    'squadra_casa':   (m['homeTeam']['name'] or '').upper(),
                    'squadra_ospite': (m['awayTeam']['name'] or '').upper(),
                    'girone':         m.get('group', '').replace('GROUP_', '').replace('GROUP ', ''),
                    'data_ora':       m.get('utcDate', ''),
                })
        if request.form.get('conferma') == '1' and partite_api:
            sel = request.form.getlist('seleziona[]')
            partite_sel = [partite_api[int(i)] for i in sel
                           if i.isdigit() and int(i) < len(partite_api)]
            if partite_sel:
                with db_conn() as conn:
                    for p in partite_sel:
                        db_execute(conn,
                                   'INSERT INTO partite (giornata, fase, girone, squadra_casa, '
                                   'squadra_ospite, pronosticabile, data_ora_partita) '
                                   'VALUES (?,\'gironi\',?,?,?,TRUE,?)',
                                   (giornata_sel, p['girone'],
                                    p['squadra_casa'], p['squadra_ospite'], p['data_ora']))
                    if USE_POSTGRES:
                        db_execute(conn,
                                   'INSERT INTO stato_giornata (giornata, is_attiva) VALUES (?,TRUE) '
                                   'ON CONFLICT (giornata) DO UPDATE SET is_attiva=TRUE',
                                   (giornata_sel,))
                        db_execute(conn,
                                   'UPDATE stato_giornata SET is_attiva=FALSE WHERE giornata!=?',
                                   (giornata_sel,))
                    else:
                        db_execute(conn,
                                   'INSERT OR IGNORE INTO stato_giornata (giornata, is_attiva) VALUES (?,1)',
                                   (giornata_sel,))
                        db_execute(conn,
                                   'UPDATE stato_giornata SET is_attiva=1 WHERE giornata=?',
                                   (giornata_sel,))
                        db_execute(conn,
                                   'UPDATE stato_giornata SET is_attiva=0 WHERE giornata!=?',
                                   (giornata_sel,))
                    db_commit(conn)
                flash(f'Giornata {giornata_sel}: {len(partite_sel)} partite importate.', 'success')
                return redirect(url_for('admin.admin_home'))

    return render_template('admin_importa_giornata.html',
                           partite_api=partite_api,
                           giornata_sel=giornata_sel, session=session)


@admin_bp.route('/admin/risultati-gironi/<int:giornata>', methods=['POST'],
                endpoint='admin_risultati_gironi')
def admin_risultati_gironi(giornata):
    if require_admin(): return 'Accesso negato.', 403
    with db_conn() as conn:
        partite = db_fetchall(conn,
                              'SELECT * FROM partite WHERE giornata=? AND pronosticabile=TRUE',
                              (giornata,))
        for p in partite:
            pid    = row_get(p, 'id')
            r_casa = _safe_int(request.form.get(f'casa_{pid}', '').strip(), lo=0, hi=20)
            r_osp  = _safe_int(request.form.get(f'ospite_{pid}', '').strip(), lo=0, hi=20)
            marc_l = request.form.getlist(f'marcatore_{pid}[]')
            marc_v = [m.strip() for m in marc_l if m.strip() not in ('', 'Nessun marcatore', 'Autogol')]
            if not marc_v:
                sp = [m.strip() for m in marc_l if m.strip() in ('Nessun marcatore', 'Autogol')]
                marc = sp[0] if sp else None
            else:
                marc = ', '.join(marc_v)
            db_execute(conn,
                       'UPDATE partite SET risultato_casa_reale=?, risultato_ospite_reale=?, '
                       'marcatore_reale=? WHERE id=?',
                       (r_casa, r_osp, marc, pid))
        db_commit(conn)
    flash('Risultati salvati!', 'success')
    return redirect(url_for('admin.admin_gestisci_partite'))


@admin_bp.route('/admin/calcola-punti-giornata/<int:giornata>', methods=['POST'],
                endpoint='admin_calcola_punti_giornata')
def admin_calcola_punti_giornata(giornata):
    if require_admin(): return 'Accesso negato.', 403
    flash(calcola_e_aggiorna_punti_giornata(giornata), 'success')
    return redirect(url_for('admin.admin_home'))


@admin_bp.route('/admin/archivia-giornata/<int:giornata>', methods=['POST'],
                endpoint='archivia_giornata')
def archivia_giornata(giornata):
    if require_admin(): return 'Accesso negato.', 403
    with db_conn() as conn:
        db_execute(conn,
                   'UPDATE stato_giornata SET is_attiva=FALSE, is_in_archivio=TRUE WHERE giornata=?',
                   (giornata,))
        db_commit(conn)
    flash(f'Giornata {giornata} archiviata.', 'success')
    return redirect(url_for('admin.admin_home'))


# ─── Gestione fasi knockout ───────────────────────────────────────────────────

@admin_bp.route('/admin/gestisci-bracket', endpoint='admin_gestisci_bracket')
def admin_gestisci_bracket():
    if require_admin(): return 'Accesso negato.', 403
    with db_conn() as conn:
        fasi_partite = {}
        for fase in ['r32', 'r16', 'qf', 'sf', 'finale', '3posto']:
            fasi_partite[fase] = db_fetchall(conn,
                'SELECT * FROM partite WHERE fase=? ORDER BY data_ora_partita', (fase,))
        stati_fasi = {row_get(r, 'fase'): r for r in db_fetchall(conn, 'SELECT * FROM stato_fase')}
    return render_template('admin_bracket.html',
                           fasi_partite=fasi_partite, stati_fasi=stati_fasi,
                           FASI_NOMI=FASI_NOMI, FASI_ORDINE=['r32','r16','qf','sf','finale','3posto'],
                           session=session)


@admin_bp.route('/admin/aggiungi-partita-knockout', methods=['POST'],
                endpoint='aggiungi_partita_knockout')
def aggiungi_partita_knockout():
    if require_admin(): return 'Accesso negato.', 403
    fase     = request.form.get('fase', '').strip()
    squadra_casa   = (request.form.get('squadra_casa') or 'TBD').upper()
    squadra_ospite = (request.form.get('squadra_ospite') or 'TBD').upper()
    data_ora = request.form.get('data_ora_partita') or None
    if fase not in ['r32', 'r16', 'qf', 'sf', 'finale', '3posto']:
        flash('Fase non valida.', 'warning')
        return redirect(url_for('admin.admin_gestisci_bracket'))
    with db_conn() as conn:
        db_execute(conn,
                   'INSERT INTO partite (fase, squadra_casa, squadra_ospite, data_ora_partita) '
                   'VALUES (?,?,?,?)',
                   (fase, squadra_casa, squadra_ospite, data_ora))
        db_commit(conn)
    flash(f'Partita aggiunta alla fase {FASI_NOMI.get(fase, fase)}.', 'success')
    return redirect(url_for('admin.admin_gestisci_bracket'))


@admin_bp.route('/admin/risultato-knockout/<int:id_partita>', methods=['POST'],
                endpoint='admin_risultato_knockout')
def admin_risultato_knockout(id_partita):
    """Inserisce il risultato di un match knockout: vincitore + gol nei 90'."""
    if require_admin(): return 'Accesso negato.', 403
    vincitore = (request.form.get('vincitore') or '').strip()
    gc90 = _safe_int(request.form.get('gol_casa_90', '').strip(), lo=0, hi=20)
    go90 = _safe_int(request.form.get('gol_ospite_90', '').strip(), lo=0, hi=20)
    if not vincitore:
        flash('Inserisci il vincitore.', 'warning')
        return redirect(url_for('admin.admin_gestisci_bracket'))
    with db_conn() as conn:
        db_execute(conn,
                   'UPDATE partite SET vincitore=?, gol_casa_90=?, gol_ospite_90=? WHERE id=?',
                   (vincitore, gc90, go90, id_partita))
        db_commit(conn)
    flash('Risultato salvato.', 'success')
    return redirect(url_for('admin.admin_gestisci_bracket'))


@admin_bp.route('/admin/attiva-fase/<fase>', methods=['POST'],
                endpoint='admin_attiva_fase')
def admin_attiva_fase(fase):
    """Attiva una fase knockout e blocca i pronostici dell'eliminazione per quella fase."""
    if require_admin(): return 'Accesso negato.', 403
    with db_conn() as conn:
        # Disattiva tutto, attiva la fase richiesta
        db_execute(conn, 'UPDATE stato_fase SET is_attiva=FALSE')
        if USE_POSTGRES:
            db_execute(conn,
                       'INSERT INTO stato_fase (fase, is_attiva, pronostici_locked) VALUES (?,TRUE,FALSE) '
                       'ON CONFLICT (fase) DO UPDATE SET is_attiva=TRUE',
                       (fase,))
        else:
            db_execute(conn,
                       'INSERT OR IGNORE INTO stato_fase (fase, is_attiva, pronostici_locked) VALUES (?,1,0)',
                       (fase,))
            db_execute(conn, 'UPDATE stato_fase SET is_attiva=1 WHERE fase=?', (fase,))
        # Disattiva giornata gironi
        db_execute(conn, 'UPDATE stato_giornata SET is_attiva=FALSE')
        db_commit(conn)
    flash(f'{FASI_NOMI.get(fase, fase)} attivata. Apri i pronostici separatamente.', 'success')
    return redirect(url_for('admin.admin_home'))


@admin_bp.route('/admin/apri-pronostici-fase/<fase>', methods=['POST'],
                endpoint='admin_apri_pronostici_fase')
def admin_apri_pronostici_fase(fase):
    """Apre i pronostici per una fase knockout (utenti possono ora inserirli)."""
    if require_admin(): return 'Accesso negato.', 403
    with db_conn() as conn:
        if USE_POSTGRES:
            db_execute(conn,
                       'INSERT INTO stato_fase (fase, pronostici_locked) VALUES (?,FALSE) '
                       'ON CONFLICT (fase) DO UPDATE SET pronostici_locked=FALSE',
                       (fase,))
        else:
            db_execute(conn,
                       'INSERT OR IGNORE INTO stato_fase (fase, pronostici_locked) VALUES (?,0)',
                       (fase,))
            db_execute(conn, 'UPDATE stato_fase SET pronostici_locked=0 WHERE fase=?', (fase,))
        db_commit(conn)
    flash(f'Pronostici {FASI_NOMI.get(fase, fase)} aperti.', 'success')
    return redirect(url_for('admin.admin_home'))


@admin_bp.route('/admin/chiudi-pronostici-fase/<fase>', methods=['POST'],
                endpoint='admin_chiudi_pronostici_fase')
def admin_chiudi_pronostici_fase(fase):
    """Chiude i pronostici (lock) per una fase knockout — fa partire le partite."""
    if require_admin(): return 'Accesso negato.', 403
    with db_conn() as conn:
        if USE_POSTGRES:
            db_execute(conn,
                       'INSERT INTO stato_fase (fase, pronostici_locked) VALUES (?,TRUE) '
                       'ON CONFLICT (fase) DO UPDATE SET pronostici_locked=TRUE',
                       (fase,))
        else:
            db_execute(conn,
                       'INSERT OR IGNORE INTO stato_fase (fase, pronostici_locked) VALUES (?,1)',
                       (fase,))
            db_execute(conn, 'UPDATE stato_fase SET pronostici_locked=1 WHERE fase=?', (fase,))
        db_commit(conn)
    flash(f'Pronostici {FASI_NOMI.get(fase, fase)} chiusi.', 'success')
    return redirect(url_for('admin.admin_home'))


@admin_bp.route('/admin/calcola-punti-fase/<fase>', methods=['POST'],
                endpoint='admin_calcola_punti_fase')
def admin_calcola_punti_fase(fase):
    if require_admin(): return 'Accesso negato.', 403
    flash(calcola_e_aggiorna_punti_fase(fase), 'success')
    return redirect(url_for('admin.admin_home'))


@admin_bp.route('/admin/archivia-fase/<fase>', methods=['POST'],
                endpoint='admin_archivia_fase')
def admin_archivia_fase(fase):
    if require_admin(): return 'Accesso negato.', 403
    with db_conn() as conn:
        if USE_POSTGRES:
            db_execute(conn,
                       'INSERT INTO stato_fase (fase, is_attiva, is_in_archivio) VALUES (?,FALSE,TRUE) '
                       'ON CONFLICT (fase) DO UPDATE SET is_attiva=FALSE, is_in_archivio=TRUE',
                       (fase,))
        else:
            db_execute(conn,
                       'INSERT OR IGNORE INTO stato_fase (fase, is_attiva, is_in_archivio) VALUES (?,0,1)',
                       (fase,))
            db_execute(conn,
                       'UPDATE stato_fase SET is_attiva=0, is_in_archivio=1 WHERE fase=?',
                       (fase,))
        db_commit(conn)
    flash(f'{FASI_NOMI.get(fase, fase)} archiviata.', 'success')
    return redirect(url_for('admin.admin_home'))


# ─── Pronostici torneo ────────────────────────────────────────────────────────

@admin_bp.route('/admin/blocca-pronostici-torneo', methods=['POST'],
                endpoint='admin_blocca_torneo')
def admin_blocca_torneo():
    if require_admin(): return 'Accesso negato.', 403
    with db_conn() as conn:
        db_execute(conn, 'UPDATE stato_pronostici_torneo SET is_locked=TRUE WHERE id=1')
        db_commit(conn)
    flash('Pronostici torneo bloccati.', 'success')
    return redirect(url_for('admin.admin_home'))


@admin_bp.route('/admin/sblocca-pronostici-torneo', methods=['POST'],
                endpoint='admin_sblocca_torneo')
def admin_sblocca_torneo():
    if require_admin(): return 'Accesso negato.', 403
    with db_conn() as conn:
        db_execute(conn, 'UPDATE stato_pronostici_torneo SET is_locked=FALSE WHERE id=1')
        db_commit(conn)
    flash('Pronostici torneo sbloccati.', 'success')
    return redirect(url_for('admin.admin_home'))


@admin_bp.route('/admin/risultati-torneo', methods=['GET', 'POST'],
                endpoint='admin_risultati_torneo')
def admin_risultati_torneo():
    if require_admin(): return 'Accesso negato.', 403
    with db_conn() as conn:
        if request.method == 'POST':
            db_execute(conn,
                       'UPDATE risultati_torneo SET vincitore=?, finalista=?, '
                       'semifinalista_1=?, semifinalista_2=?, capocannoniere=? WHERE id=1',
                       (request.form.get('vincitore'), request.form.get('finalista'),
                        request.form.get('semifinalista_1'), request.form.get('semifinalista_2'),
                        request.form.get('capocannoniere')))
            db_commit(conn)
            msg = calcola_e_aggiorna_punti_torneo()
            flash(msg, 'success')
            return redirect(url_for('admin.admin_home'))
        rf = db_fetchone(conn, 'SELECT * FROM risultati_torneo WHERE id=1')
    return render_template('admin_risultati_torneo.html', rf=rf, session=session)


# ─── Ricalcola ────────────────────────────────────────────────────────────────

@admin_bp.route('/admin/ricalcola-tutto', methods=['POST'],
                endpoint='admin_ricalcola_tutto')
def admin_ricalcola_tutto():
    if require_admin(): return 'Accesso negato.', 403
    flash(ricalcola_tutto(), 'success')
    return redirect(url_for('admin.admin_home'))


# ─── Toggle pronosticabile ───────────────────────────────────────────────────

@admin_bp.route('/admin/toggle-pronosticabile/<int:id_partita>', methods=['POST'],
                endpoint='admin_toggle_pronosticabile')
def admin_toggle_pronosticabile(id_partita):
    if require_admin(): return 'Accesso negato.', 403
    with db_conn() as conn:
        p = db_fetchone(conn, 'SELECT pronosticabile, giornata FROM partite WHERE id=?',
                        (id_partita,))
        if not p:
            flash('Partita non trovata.', 'danger')
            return redirect(url_for('admin.admin_gestisci_partite'))
        nuovo = not row_get(p, 'pronosticabile')
        db_execute(conn,
                   'UPDATE partite SET pronosticabile=? WHERE id=?',
                   (nuovo, id_partita))
        db_commit(conn)
    return redirect(request.referrer or url_for('admin.admin_gestisci_partite'))


# ─── Importa risultati da API ─────────────────────────────────────────────────

@admin_bp.route('/admin/importa-risultati/<int:giornata>', methods=['POST'],
                endpoint='admin_importa_risultati')
def admin_importa_risultati(giornata):
    """Scarica e aggiorna i risultati delle partite pronosticabili da football-data.org."""
    if require_admin(): return 'Accesso negato.', 403
    from flask import current_app
    code = current_app.config.get('COMPETITION_CODE', 'WC')
    data, err = _api_get(f'/competitions/{code}/matches', {'matchday': giornata})
    if err:
        flash(f'Errore API: {err}', 'danger')
        return redirect(url_for('admin.admin_gestisci_partite', giornata=giornata))
    
    aggiornate = 0
    with db_conn() as conn:
        partite_db = db_fetchall(conn,
            'SELECT * FROM partite WHERE giornata=? AND pronosticabile=TRUE',
            (giornata,))
        
        for m in (data or {}).get('matches', []):
            score = m.get('score', {})
            ft    = score.get('fullTime', {})
            casa  = ft.get('home')
            osp   = ft.get('away')
            
            if casa is None or osp is None:
                continue
                
            nome_casa  = (m['homeTeam']['name'] or '').upper()
            nome_ospite = (m['awayTeam']['name'] or '').upper()
            
            # --- NUOVA LOGICA: Estrazione Marcatori ---
            marcatori_list = []
            goals = m.get('goals', [])
            for g in goals:
                scorer = g.get('scorer', {})
                nome_marcatore = scorer.get('name')
                if nome_marcatore:
                    marcatori_list.append(nome_marcatore)
            
            marcatore_str = ", ".join(marcatori_list) if marcatori_list else None
            
            # Se la partita finisce 0-0, forza "Nessun marcatore"
            if casa == 0 and osp == 0:
                marcatore_str = "Nessun marcatore"
            # ------------------------------------------

            for p in partite_db:
                if (row_get(p, 'squadra_casa') == nome_casa and
                        row_get(p, 'squadra_ospite') == nome_ospite):
                    
                    # Aggiorniamo anche il campo marcatore_reale
                    db_execute(conn,
                               'UPDATE partite SET risultato_casa_reale=?, '
                               'risultato_ospite_reale=?, marcatore_reale=? WHERE id=?',
                               (casa, osp, marcatore_str, row_get(p, 'id')))
                    aggiornate += 1
        db_commit(conn)
        
    flash(f'Risultati aggiornati da API: {aggiornate} partite.', 'success')
    return redirect(url_for('admin.admin_gestisci_partite', giornata=giornata))

# ─── Pagina pronostici torneo admin ──────────────────────────────────────────

@admin_bp.route('/admin/pronostici-torneo-pg', endpoint='admin_pronostici_torneo_pg')
def admin_pronostici_torneo_pg():
    if require_admin(): return 'Accesso negato.', 403
    with db_conn() as conn:
        lock_row = db_fetchone(conn,
            'SELECT is_locked FROM stato_pronostici_torneo WHERE id=1')
        torneo_locked = row_get(lock_row, 'is_locked') if lock_row else False
        tutti = db_fetchall(conn,
            'SELECT u.nome_utente, pt.* FROM utenti u '
            'LEFT JOIN pronostici_torneo pt ON u.id=pt.id_utente '
            'ORDER BY u.nome_utente')
    return render_template('admin_pronostici_torneo_pg.html',
                           torneo_locked=torneo_locked,
                           tutti=tutti, session=session)


# ─── Reminder manuale fase knockout ──────────────────────────────────────────

@admin_bp.route('/admin/reminder-manuale-fase/<fase>', methods=['POST'],
                endpoint='admin_invia_reminder_manuale_fase')
def admin_invia_reminder_manuale_fase(fase):
    if require_admin(): return 'Accesso negato.', 403
    try:
        with db_conn() as conn:
            partite = db_fetchall(conn,
                'SELECT squadra_casa, squadra_ospite, data_ora_partita '
                'FROM partite WHERE fase=?', (fase,))
            utenti_email = db_fetchall(conn,
                "SELECT email FROM utenti WHERE email IS NOT NULL AND email != ''")
        destinatari = [row_get(u, 'email') for u in utenti_email if row_get(u, 'email')]
        if not destinatari:
            flash('Nessun utente con email.', 'warning')
            return redirect(url_for('admin.admin_home'))
        from services.email_service import invia_email_async, build_email_giornata
        pl = [{'squadra_casa': row_get(p, 'squadra_casa'),
               'squadra_ospite': row_get(p, 'squadra_ospite'),
               'data_ora_partita': row_get(p, 'data_ora_partita')} for p in partite]
        from services.game_logic import FASI_NOMI
        invia_email_async(
            destinatari,
            f'🏆 Fanta Mondiali — {FASI_NOMI.get(fase, fase)}: inserisci i pronostici!',
            build_email_giornata(fase, pl))
        flash(f'Reminder {FASI_NOMI.get(fase, fase)} inviato a {len(destinatari)} utenti!',
              'success')
    except Exception as e:
        flash(f'Errore: {e}', 'danger')
    return redirect(url_for('admin.admin_home'))


# ─── Pagine separate admin ────────────────────────────────────────────────────

@admin_bp.route('/admin/importa-giocatori-pg', endpoint='admin_importa_giocatori_pg')
def admin_importa_giocatori_pg():
    if require_admin(): return 'Accesso negato.', 403
    with db_conn() as conn:
        r1 = db_fetchone(conn, 'SELECT COUNT(*) AS c FROM giocatori')
        r2 = db_fetchone(conn, 'SELECT COUNT(DISTINCT squadra) AS c FROM giocatori')
    return render_template('admin_importa_giocatori_pg.html',
                           giocatori_count=row_get(r1, 'c') or 0,
                           squadre_count=row_get(r2, 'c') or 0,
                           session=session)


@admin_bp.route('/admin/gestisci-fasi-pg', endpoint='admin_gestisci_fasi_pg')
def admin_gestisci_fasi_pg():
    if require_admin(): return 'Accesso negato.', 403
    with db_conn() as conn:
        stati_fasi = {row_get(r, 'fase'): r
                      for r in db_fetchall(conn, 'SELECT * FROM stato_fase')}
    return render_template('admin_gestisci_fasi_pg.html',
                           fasi=['r32', 'r16', 'qf', 'sf', 'finale', '3posto'],
                           stati_fasi=stati_fasi,
                           FASI_NOMI=FASI_NOMI, session=session)


@admin_bp.route('/admin/ricalcola-pg', endpoint='admin_ricalcola_pg')
def admin_ricalcola_pg():
    if require_admin(): return 'Accesso negato.', 403
    return render_template('admin_ricalcola_pg.html', session=session)


@admin_bp.route('/admin/reminder-manuale/<int:giornata>', methods=['POST'],
                endpoint='admin_invia_reminder_manuale')
def admin_invia_reminder_manuale(giornata):
    """Invia reminder manuale per una giornata gironi."""
    if require_admin(): return 'Accesso negato.', 403
    try:
        with db_conn() as conn:
            partite = db_fetchall(conn,
                'SELECT squadra_casa, squadra_ospite, data_ora_partita '
                'FROM partite WHERE giornata=? AND pronosticabile=TRUE', (giornata,))
            utenti_email = db_fetchall(conn,
                "SELECT email FROM utenti WHERE email IS NOT NULL AND email != ''")
        destinatari = [row_get(u, 'email') for u in utenti_email if row_get(u, 'email')]
        if not destinatari:
            flash('Nessun utente con email registrata.', 'warning')
            return redirect(url_for('admin.admin_home'))
        from services.email_service import invia_email_async, build_email_giornata
        pl = [{'squadra_casa': row_get(p, 'squadra_casa'),
               'squadra_ospite': row_get(p, 'squadra_ospite'),
               'data_ora_partita': row_get(p, 'data_ora_partita')} for p in partite]
        invia_email_async(
            destinatari,
            f'⚽ Fanta Mondiali — Round {giornata}: inserisci i pronostici!',
            build_email_giornata(giornata, pl))
        flash(f'Reminder inviato a {len(destinatari)} utenti!', 'success')
    except Exception as e:
        log.exception('Errore reminder manuale')
        flash(f'Errore: {e}', 'danger')
    return redirect(url_for('admin.admin_home'))


# ─── Importazione rose squadre ────────────────────────────────────────────────

@admin_bp.route('/admin/importa-giocatori', methods=['POST'],
                endpoint='admin_importa_giocatori')
def admin_importa_giocatori():
    """
    Importa le rose di tutte le squadre WC 2026 dall'API football-data.org.
    Gira in background per rispettare il rate limit (10 req/min piano free).
    Stima: ~6-7 minuti per 48 squadre.
    """
    if require_admin(): return 'Accesso negato.', 403

    from flask import current_app
    app = current_app._get_current_object()

    def _importa_rose():
        import time as _time
        with app.app_context():
            log.info('[ROSE] ▶ Avvio importazione rose WC 2026...')
            try:
                code = app.config.get('COMPETITION_CODE', 'WC')

                # Step 1: ottieni la lista completa delle 48 squadre
                data, err = _api_get(f'/competitions/{code}/teams')
                if err:
                    log.error(f'[ROSE] Errore lista squadre: {err}')
                    return
                teams = data.get('teams', [])
                log.info(f'[ROSE] Trovate {len(teams)} squadre')

                if not teams:
                    log.warning('[ROSE] Nessuna squadra restituita dall\'API.')
                    return

                # Step 2: svuota la tabella giocatori
                with db_conn() as conn:
                    db_execute(conn, 'DELETE FROM giocatori')
                    db_commit(conn)
                log.info('[ROSE] Tabella giocatori svuotata.')

                # Step 3: per ogni squadra recupera la rosa individualmente
                # (piano free: /teams/{id} restituisce squad completa)
                totale_inseriti = 0
                richieste_fatte = 1  # già fatto la chiamata /competitions/WC/teams

                for i, team in enumerate(teams):
                    team_id   = team.get('id')
                    # Usa shortName se disponibile (più corto), altrimenti name
                    team_name = (team.get('shortName') or team.get('name') or '').upper()

                    # Prova prima a usare squad già nella risposta /competitions/teams
                    squad = team.get('squad', [])

                    # Se vuoto (frequente su piano free), chiama /teams/{id}
                    if not squad and team_id:
                        # Rate limiting: max 10 req/min → aspetta se necessario
                        richieste_fatte += 1
                        if richieste_fatte % 9 == 0:
                            log.info(f'[ROSE] Pausa rate limit dopo {richieste_fatte} chiamate...')
                            _time.sleep(65)
                        else:
                            _time.sleep(7)  # ~8-9 req/min, sicuro

                        team_data, err2 = _api_get(f'/teams/{team_id}')
                        if err2:
                            log.warning(f'[ROSE] Errore per {team_name}: {err2}')
                            continue
                        squad = (team_data or {}).get('squad', [])

                    if not squad:
                        log.warning(f'[ROSE] Rosa vuota per {team_name} (id={team_id})')
                        continue

                    # Inserisci i giocatori nel DB
                    inseriti = 0
                    with db_conn() as conn:
                        for player in squad:
                            nome = (player.get('name') or '').strip()
                            if nome:
                                db_execute(conn,
                                           'INSERT INTO giocatori (nome_giocatore, squadra) '
                                           'VALUES (?, ?)',
                                           (nome, team_name))
                                inseriti += 1
                        db_commit(conn)

                    totale_inseriti += inseriti
                    log.info(
                        f'[ROSE] {team_name}: {inseriti} giocatori '
                        f'({i + 1}/{len(teams)})'
                    )

                log.info(
                    f'[ROSE] ✅ Completato: {totale_inseriti} giocatori '
                    f'da {len(teams)} squadre.'
                )

            except Exception:
                log.exception('[ROSE] Errore importazione rose')

    threading.Thread(target=_importa_rose, daemon=True).start()
    flash(
        '🌍 Importazione rose avviata in background (~6-7 minuti per il '
        'rate limiting API). Controlla i log di Render per il progresso.',
        'info'
    )
    return redirect(url_for('admin.admin_home'))
# ─── Selezione Massiva Pronosticabili ─────────────────────────────────────────

@admin_bp.route('/admin/massivo-pronosticabile/<int:giornata>/<int:stato>', methods=['POST'], endpoint='admin_massivo_pronosticabile')
def admin_massivo_pronosticabile(giornata, stato):
    from flask import flash, redirect, url_for
    from db_utils import db_conn, db_execute, db_commit
    if require_admin(): return "Accesso negato.", 403
    nuovo_stato = bool(stato)
    with db_conn() as conn:
        db_execute(conn, "UPDATE partite SET pronosticabile=? WHERE giornata=?", (nuovo_stato, giornata))
        db_commit(conn)
    azione = "aperte" if nuovo_stato else "chiuse"
    flash(f"Tutte le partite del Round {giornata} sono state {azione} per i pronostici.", "success")
    return redirect(url_for('admin.admin_gestisci_partite', giornata=giornata))


# ─── Gestione Pronostici Partite (Gironi) ─────────────────────────────────────

@admin_bp.route('/admin/pronostici-partite', methods=['GET', 'POST'], endpoint='admin_pronostici_partite')
def admin_pronostici_partite():
    from flask import request, flash, redirect, url_for, render_template, session
    from db_utils import db_conn, db_fetchall, db_fetchone, db_execute, db_commit, row_get
    from services.game_logic import _safe_int  # <-- Aggiunto per evitare i crash sui campi vuoti

    if require_admin(): return "Accesso negato.", 403

    with db_conn() as conn:
        partite = db_fetchall(conn, "SELECT id, giornata, squadra_casa, squadra_ospite FROM partite WHERE fase='gironi' ORDER BY giornata, data_ora_partita")

        partita_id = request.args.get('partita_id', type=int)
        partita_sel = None
        utenti_pronostici = []

        if partita_id:
            partita_sel = db_fetchone(conn, "SELECT * FROM partite WHERE id=?", (partita_id,))

            if request.method == 'POST':
                for key in request.form:
                    if key.startswith('gc_'):
                        try:
                            uid = int(key.split('_')[1])
                            gc_raw = request.form.get(f'gc_{uid}', '').strip()
                            go_raw = request.form.get(f'go_{uid}', '').strip()
                            esito = request.form.get(f'esito_{uid}', '').strip()
                            marc_raw = request.form.get(f'marc_{uid}', '').strip()
                            
                            # Trasforma in numeri veri o lascia None se è vuoto
                            gc = _safe_int(gc_raw, lo=0, hi=20)
                            go = _safe_int(go_raw, lo=0, hi=20)
                            marc = marc_raw if marc_raw else None

                            # Calcolo esito automatico
                            if gc is not None and go is not None:
                                if gc > go: esito = '1'
                                elif gc < go: esito = '2'
                                else: esito = 'X'
                            else:
                                esito = esito if esito else None

                            if esito is not None or (gc is not None and go is not None) or marc is not None:
                                exists = db_fetchone(conn, "SELECT id FROM pronostici_giornata WHERE id_utente=? AND id_partita=?", (uid, partita_id))
                                if exists:
                                    db_execute(conn, "UPDATE pronostici_giornata SET risultato_casa_pronosticato=?, risultato_ospite_pronosticato=?, marcatore_pronosticato=?, esito_pronosticato=? WHERE id_utente=? AND id_partita=?", (gc, go, marc, esito, uid, partita_id))
                                else:
                                    db_execute(conn, "INSERT INTO pronostici_giornata (id_utente, id_partita, risultato_casa_pronosticato, risultato_ospite_pronosticato, marcatore_pronosticato, esito_pronosticato) VALUES (?, ?, ?, ?, ?, ?)", (uid, partita_id, gc, go, marc, esito))
                            elif gc is None and go is None and not esito and not marc:
                                db_execute(conn, "DELETE FROM pronostici_giornata WHERE id_utente=? AND id_partita=?", (uid, partita_id))
                        except Exception as e:
                            log.exception(f"Errore salvataggio admin pronostici per l'utente con ID {uid}: {e}")
                
                db_commit(conn)
                flash("Pronostici salvati con successo!", "success")
                return redirect(url_for('admin.admin_pronostici_partite', partita_id=partita_id))

            utenti = db_fetchall(conn, "SELECT id, nome_utente FROM utenti ORDER BY nome_utente")
            
            tutti_pronostici = db_fetchall(conn, "SELECT * FROM pronostici_giornata WHERE id_partita=?", (partita_id,))
            pron = {}
            for p in tutti_pronostici:
                try:
                    p_uid = int(row_get(p, 'id_utente'))
                    pron[p_uid] = p
                except Exception:
                    pass

            for u in utenti:
                try:
                    uid = int(row_get(u, 'id'))
                except Exception:
                    continue
                    
                p = pron.get(uid)
                
                esito_val = ''
                gc_val = ''
                go_val = ''
                marc_val = ''

                if p:
                    e = row_get(p, 'esito_pronosticato')
                    c = row_get(p, 'risultato_casa_pronosticato')
                    o = row_get(p, 'risultato_ospite_pronosticato')
                    m = row_get(p, 'marcatore_pronosticato')
                    
                    if e is not None and str(e).lower() != 'none': esito_val = str(e)
                    if c is not None and str(c).lower() != 'none': gc_val = str(c)
                    if o is not None and str(o).lower() != 'none': go_val = str(o)
                    if m is not None and str(m).lower() != 'none': marc_val = str(m)

                utenti_pronostici.append({
                    'id': uid,
                    'nome_utente': row_get(u, 'nome_utente'),
                    'esito': esito_val,
                    'gol_casa': gc_val,
                    'gol_ospite': go_val,
                    'marcatore': marc_val
                })

    return render_template('admin_pronostici_partite.html', partite=partite, partita_sel=partita_sel, utenti_pronostici=utenti_pronostici, session=session)
@admin_bp.route('/admin/attiva-giornata', methods=['POST'], endpoint='admin_attiva_giornata')
def admin_attiva_giornata():
    """Attiva un round dei gironi (e spegne eventuali fasi KO attive)"""
    if require_admin(): 
        return 'Accesso negato.', 403
        
    giornata = _safe_int(request.form.get('giornata'))
    if not giornata:
        flash('Seleziona un round valido da attivare.', 'warning')
        return redirect(url_for('admin.admin_home'))
        
    with db_conn() as conn:
        # 1. Disattiva tutte le giornate dei gironi e le fasi eliminazione
        db_execute(conn, 'UPDATE stato_giornata SET is_attiva=FALSE')
        db_execute(conn, 'UPDATE stato_fase SET is_attiva=FALSE')
        
        # 2. Attiva la giornata selezionata
        if USE_POSTGRES:
            db_execute(conn,
                       'INSERT INTO stato_giornata (giornata, is_attiva, is_in_archivio) '
                       'VALUES (?, TRUE, FALSE) '
                       'ON CONFLICT (giornata) DO UPDATE SET is_attiva=TRUE',
                       (giornata,))
        else:
            # Per SQLite backend locale
            db_execute(conn, 'INSERT OR IGNORE INTO stato_giornata (giornata, is_attiva, is_in_archivio) VALUES (?, 0, 0)', (giornata,))
            db_execute(conn, 'UPDATE stato_giornata SET is_attiva=TRUE WHERE giornata=?', (giornata,))
            
        db_commit(conn)
        
    flash(f'✅ Round {giornata} attivato con successo!', 'success')
    return redirect(url_for('admin.admin_home'))
@admin_bp.route('/admin/modifica-giornata-archiviata/<int:giornata>', methods=['GET', 'POST'], endpoint='admin_modifica_giornata_archiviata')
def admin_modifica_giornata_archiviata(giornata):
    """Riapre una giornata archiviata rimettendola come attiva nella Dashboard"""
    if require_admin(): 
        return 'Accesso negato.', 403
        
    with db_conn() as conn:
        # 1. Disattiva temporaneamente qualsiasi altra giornata o fase attiva
        db_execute(conn, 'UPDATE stato_giornata SET is_attiva=FALSE')
        db_execute(conn, 'UPDATE stato_fase SET is_attiva=FALSE')
        
        # 2. Riattiva la giornata selezionata e la rimuove dall'archivio
        if USE_POSTGRES:
            db_execute(conn, 'UPDATE stato_giornata SET is_attiva=TRUE, is_in_archivio=FALSE WHERE giornata=?', (giornata,))
        else:
            db_execute(conn, 'UPDATE stato_giornata SET is_attiva=1, is_in_archivio=0 WHERE giornata=?', (giornata,))
            
        db_commit(conn)
        
    flash(f'🔄 Round {giornata} riaperto! Ora puoi modificare i risultati dalla Home Admin.', 'success')
    return redirect(url_for('admin.admin_home'))
@admin_bp.route('/admin/importa-api-knockout/<fase>', methods=['POST'], endpoint='admin_importa_api_knockout')
def admin_importa_api_knockout(fase):
    """Scarica in automatico le partite della fase a eliminazione diretta dall'API."""
    if require_admin(): return 'Accesso negato.', 403
    from flask import current_app
    code = current_app.config.get('COMPETITION_CODE', 'WC')
    
    # Richiesta di tutte le partite del torneo all'API
    data, err = _api_get(f'/competitions/{code}/matches')
    if err:
        flash(f'Errore API: {err}', 'danger')
        return redirect(url_for('admin.admin_home'))

    # Mappa le nostre sigle DB con i nomi ufficiali che usa l'API
    stage_map = {
        'r32': ['LAST_32', 'ROUND_OF_32'],
        'r16': ['LAST_16'],
        'qf':  ['QUARTER_FINALS'],
        'sf':  ['SEMI_FINALS'],
        'finale': ['FINAL'],
        '3posto': ['THIRD_PLACE']
    }
    target_stages = stage_map.get(fase, [])

    inserite = 0
    with db_conn() as conn:
        for m in (data or {}).get('matches', []):
            stage = m.get('stage')
            if stage in target_stages:
                casa = (m.get('homeTeam', {}).get('name') or 'TBD').upper()
                ospite = (m.get('awayTeam', {}).get('name') or 'TBD').upper()
                data_ora = m.get('utcDate', '')

                # Evita duplicati controllando se la partita è già nel DB
                exists = db_fetchone(conn,
                    'SELECT id FROM partite WHERE fase=? AND squadra_casa=? AND squadra_ospite=?',
                    (fase, casa, ospite))

                if not exists:
                    # TRUCCO: Inseriamo giornata=0 per accontentare il vincolo NOT NULL del database
                    db_execute(conn,
                        'INSERT INTO partite (fase, squadra_casa, squadra_ospite, data_ora_partita, pronosticabile, giornata) '
                        'VALUES (?,?,?,?,FALSE,0)',
                        (fase, casa, ospite, data_ora))
                    inserite += 1
        db_commit(conn)

    if inserite > 0:
        flash(f'Magia fatta! {inserite} partite importate per {FASI_NOMI.get(fase, fase)}.', 'success')
    else:
        flash(f'Nessuna nuova partita trovata nell\'API per questa fase. (O l\'API non è ancora aggiornata, o le hai già importate).', 'warning')

    return redirect(url_for('admin.admin_home'))
