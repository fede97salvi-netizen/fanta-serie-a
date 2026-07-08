"""
Blueprint gioco — route dedicate al giocatore (non admin).

Route:
  /classifica
  /giornate
  /giornata/<n>
  /giornata/<n>/classifica-cumulativa
  /pronostici-iniziali
  /pronostici-giornata/<n>
"""

import logging
from datetime import datetime

import pytz
from flask import (
    Blueprint, render_template, request, redirect, url_for, session,
)

from db_utils import db_conn, db_execute, db_fetchone, db_fetchall, db_commit, row_get
from services.game_logic import calcola_punti_pronostico, is_partita_scaduta
from auth_utils import login_required

log = logging.getLogger('fanta')

gioco_bp = Blueprint('gioco', __name__)


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


def _utente_corrente(conn):
    if 'nome_utente' not in session:
        return None
    return db_fetchone(conn, 'SELECT * FROM utenti WHERE nome_utente = ?',
                       (session['nome_utente'],))


# ─── Route ────────────────────────────────────────────────────────────────────

@gioco_bp.route('/classifica', endpoint='classifica')
@login_required
def classifica():
    with db_conn() as conn:
        classifica_utenti = db_fetchall(
            conn,
            'SELECT u.nome_utente, p.punteggio_totale FROM utenti u '
            'JOIN punteggi p ON u.id = p.id_utente '
            'ORDER BY p.punteggio_totale DESC',
        )
    return render_template('classifica.html',
                           classifica=classifica_utenti, session=session)


@gioco_bp.route('/giornate', endpoint='archivio_giornate')
@login_required
def archivio_giornate():
    with db_conn() as conn:
        giornate = db_fetchall(
            conn,
            'SELECT * FROM stato_giornata WHERE is_in_archivio = TRUE '
            'ORDER BY giornata',
        )
    return render_template('archivio_giornate.html',
                           giornate=giornate, session=session)


@gioco_bp.route('/giornata/<int:giornata>', endpoint='visualizza_giornata')
@login_required
def visualizza_giornata(giornata: int):
    with db_conn() as conn:
        partite_reali = db_fetchall(
            conn,
            'SELECT * FROM partite WHERE giornata = ? '
            'ORDER BY pronosticabile DESC, data_ora_partita',
            (giornata,),
        )
        partite_pron = db_fetchall(
            conn,
            'SELECT * FROM partite WHERE giornata = ? '
            'AND pronosticabile = TRUE AND risultato_casa_reale IS NOT NULL',
            (giornata,),
        )
        utenti = db_fetchall(conn, 'SELECT id, nome_utente FROM utenti')

        # Una sola query: costruisce sia l'indice (uid,pid)->pronostico
        # sia la mappa pid->{utente->dettaglio} usata dal template.
        pids = [row_get(p, 'id') for p in partite_pron]
        pronostici_idx = {}
        pronostici_per_partita = {}
        if pids:
            ph = ','.join(['?'] * len(pids))
            rows = db_fetchall(
                conn,
                'SELECT u.nome_utente, pg.* FROM pronostici_giornata pg '
                f'JOIN utenti u ON pg.id_utente = u.id '
                f'WHERE pg.id_partita IN ({ph})',
                tuple(pids),
            )
            for r in rows:
                uid = row_get(r, 'id_utente')
                pid = row_get(r, 'id_partita')
                pronostici_idx[(uid, pid)] = r
                pronostici_per_partita.setdefault(pid, {})[
                    row_get(r, 'nome_utente')] = {
                    'esito':     row_get(r, 'esito_pronosticato'),
                    'r_casa':    row_get(r, 'risultato_casa_pronosticato'),
                    'r_osp':     row_get(r, 'risultato_ospite_pronosticato'),
                    'marcatore': row_get(r, 'marcatore_pronosticato'),
                }

        classifica_giornata = []
        for utente in utenti:
            uid = row_get(utente, 'id')
            punti_per_partita = {}
            punti_utente = 0
            for partita in partite_pron:
                pid = row_get(partita, 'id')
                det = calcola_punti_pronostico(
                    pronostici_idx.get((uid, pid)), partita)
                punti_per_partita[pid] = det
                punti_utente += det['totale']
            classifica_giornata.append({
                'nome_utente':     row_get(utente, 'nome_utente'),
                'punti_totali':    punti_utente,
                'punti_per_partita': punti_per_partita,
            })
        classifica_giornata.sort(key=lambda x: x['punti_totali'], reverse=True)

    return render_template(
        'visualizza_giornata.html',
        giornata=giornata,
        partite=partite_reali,
        partite_pron=partite_pron,
        classifica=classifica_giornata,
        pronostici_per_partita=pronostici_per_partita,
        session=session,
    )


@gioco_bp.route('/giornata/<int:giornata>/classifica-cumulativa',
                endpoint='classifica_cumulativa_giornata')
@login_required
def classifica_cumulativa_giornata(giornata: int):
    with db_conn() as conn:
        rows = db_fetchall(
            conn,
            'SELECT u.nome_utente, COALESCE(SUM(pg.punti), 0) AS punteggio '
            'FROM utenti u '
            'LEFT JOIN punteggi_giornata pg '
            '  ON pg.id_utente = u.id AND pg.giornata <= ? '
            'GROUP BY u.id, u.nome_utente '
            'ORDER BY punteggio DESC, u.nome_utente',
            (giornata,),
        )
        classifica = [
            {'nome_utente': row_get(r, 'nome_utente'),
             'punteggio':   row_get(r, 'punteggio') or 0}
            for r in rows
        ]
    return render_template('classifica_cumulativa.html',
                           giornata=giornata, classifica=classifica,
                           session=session)


@gioco_bp.route('/pronostici-iniziali', methods=['GET', 'POST'],
                endpoint='pronostici_iniziali')
@login_required
def pronostici_iniziali():
    with db_conn() as conn:
        lock_row  = db_fetchone(
            conn, 'SELECT is_locked FROM stato_pronostici_iniziali WHERE id = 1')
        is_locked = row_get(lock_row, 'is_locked') if lock_row else True
        user      = _utente_corrente(conn)
        if not user:
            return redirect(url_for('auth.logout'))
        user_id = row_get(user, 'id')

        if is_locked:
            pronostici_tutti = db_fetchall(
                conn,
                'SELECT u.nome_utente, pi.* FROM pronostici_iniziali pi '
                'JOIN utenti u ON pi.id_utente = u.id ORDER BY u.nome_utente',
            )
            return render_template('pronostici_iniziali.html',
                                   is_locked=is_locked,
                                   pronostici_tutti=pronostici_tutti,
                                   session=session)

        if request.method == 'POST':
            s1 = request.form.get('squadra_1') or ''
            s2 = request.form.get('squadra_2') or ''
            s3 = request.form.get('squadra_3') or ''
            s4 = request.form.get('squadra_4') or ''
            cc = request.form.get('capocannoniere') or ''
            if db_fetchone(conn,
                           'SELECT id FROM pronostici_iniziali WHERE id_utente = ?',
                           (user_id,)):
                db_execute(
                    conn,
                    'UPDATE pronostici_iniziali '
                    'SET squadra_1=?, squadra_2=?, squadra_3=?, '
                    'squadra_4=?, capocannoniere=? WHERE id_utente=?',
                    (s1, s2, s3, s4, cc, user_id),
                )
            else:
                db_execute(
                    conn,
                    'INSERT INTO pronostici_iniziali '
                    '(id_utente, squadra_1, squadra_2, squadra_3, '
                    'squadra_4, capocannoniere) VALUES (?,?,?,?,?,?)',
                    (user_id, s1, s2, s3, s4, cc),
                )
            db_commit(conn)
            return redirect(url_for('auth.home'))

        pronostico = db_fetchone(
            conn, 'SELECT * FROM pronostici_iniziali WHERE id_utente = ?',
            (user_id,),
        )
        return render_template('pronostici_iniziali.html',
                               is_locked=is_locked,
                               pronostico=pronostico,
                               session=session)


@gioco_bp.route('/pronostici-giornata/<int:giornata>',
                methods=['GET', 'POST'],
                endpoint='pronostici_giornata')
@login_required
def pronostici_giornata(giornata: int):
    with db_conn() as conn:
        user = _utente_corrente(conn)
        if not user:
            return redirect(url_for('auth.logout'))
        user_id = row_get(user, 'id')

        partite = db_fetchall(
            conn,
            'SELECT * FROM partite WHERE giornata = ? AND pronosticabile = TRUE',
            (giornata,),
        )
        # Giocatori di tutte le squadre in UNA query (no N+1)
        giocatori_per_partita = {}
        squadre = set()
        for partita in partite:
            squadre.add((row_get(partita, 'squadra_casa')   or '').upper())
            squadre.add((row_get(partita, 'squadra_ospite') or '').upper())
        per_squadra = {}
        if squadre:
            ph = ','.join(['?'] * len(squadre))
            tutti = db_fetchall(
                conn,
                f'SELECT nome_giocatore, squadra FROM giocatori '
                f'WHERE UPPER(squadra) IN ({ph}) '
                f'ORDER BY squadra, nome_giocatore',
                tuple(squadre),
            )
            for g in tutti:
                per_squadra.setdefault(
                    (row_get(g, 'squadra') or '').upper(), []).append(g)
        for partita in partite:
            pid = row_get(partita, 'id')
            sc  = (row_get(partita, 'squadra_casa')   or '').upper()
            so  = (row_get(partita, 'squadra_ospite') or '').upper()
            giocatori_per_partita[pid] = (
                per_squadra.get(sc, []) + per_squadra.get(so, []))

        pronostici_salvati = db_fetchall(
            conn,
            'SELECT * FROM pronostici_giornata WHERE id_utente = ? '
            'AND id_partita IN (SELECT id FROM partite WHERE giornata = ?)',
            (user_id, giornata),
        )
        pronostici_dict = {row_get(p, 'id_partita'): p for p in pronostici_salvati}

        if request.method == 'POST':
            for partita in partite:
                if is_partita_scaduta(row_get(partita, 'data_ora_partita')):
                    continue
                pid    = row_get(partita, 'id')
                esito  = request.form.get(f'esito_{pid}')
                r_casa = _safe_int(request.form.get(f'risultato_casa_{pid}'),
                                   lo=0, hi=20)
                r_osp  = _safe_int(request.form.get(f'risultato_ospite_{pid}'),
                                   lo=0, hi=20)
                marc   = (request.form.get(f'marcatore_{pid}') or '').strip()
                if esito or (r_casa is not None and r_osp is not None) or marc:
                    if pid in pronostici_dict:
                        db_execute(
                            conn,
                            'UPDATE pronostici_giornata SET esito_pronosticato=?, '
                            'risultato_casa_pronosticato=?, '
                            'risultato_ospite_pronosticato=?, '
                            'marcatore_pronosticato=? '
                            'WHERE id_utente=? AND id_partita=?',
                            (esito, r_casa, r_osp, marc, user_id, pid),
                        )
                    else:
                        db_execute(
                            conn,
                            'INSERT INTO pronostici_giornata '
                            '(id_utente, id_partita, esito_pronosticato, '
                            'risultato_casa_pronosticato, '
                            'risultato_ospite_pronosticato, '
                            'marcatore_pronosticato) '
                            'VALUES (?,?,?,?,?,?)',
                            (user_id, pid, esito, r_casa, r_osp, marc),
                        )
            db_commit(conn)
            return redirect(url_for('auth.home'))

        scadenze_dict           = {}
        scaduti_pids            = []
        for partita in partite:
            pid     = row_get(partita, 'id')
            scaduto = is_partita_scaduta(row_get(partita, 'data_ora_partita'))
            scadenze_dict[pid] = scaduto
            if scaduto:
                scaduti_pids.append(pid)
        # Pronostici altrui per le partite scadute in UNA query (no N+1)
        pronostici_altri_utenti = {pid: [] for pid in scaduti_pids}
        if scaduti_pids:
            ph = ','.join(['?'] * len(scaduti_pids))
            rows = db_fetchall(
                conn,
                'SELECT u.nome_utente, pg.* FROM pronostici_giornata pg '
                f'JOIN utenti u ON pg.id_utente = u.id '
                f'WHERE pg.id_partita IN ({ph})',
                tuple(scaduti_pids),
            )
            for r in rows:
                pronostici_altri_utenti.setdefault(
                    row_get(r, 'id_partita'), []).append(r)

    return render_template(
        'pronostici_giornata.html',
        partite=partite,
        giornata=giornata,
        pronostici_per_partita=pronostici_dict,
        scadenze=scadenze_dict,
        pronostici_altri_utenti=pronostici_altri_utenti,
        giocatori_per_partita=giocatori_per_partita,
        session=session,
    )
