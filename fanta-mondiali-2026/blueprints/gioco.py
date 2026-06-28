"""
Blueprint gioco — Fanta Mondiali 2026
Route per i partecipanti.
"""

import logging
from flask import Blueprint, render_template, request, redirect, url_for, session, flash

from db_utils import db_conn, db_execute, db_fetchone, db_fetchall, db_commit, row_get
from services.game_logic import (
    calcola_punti_pronostico,
    _safe_int, is_partita_scaduta, FASI_NOMI,
    PUNTI_TORNEO, PUNTI_ESITO, PUNTI_RISULTATO, PUNTI_MARCATORE, PUNTI_BONUS_TRIPLA
)

log = logging.getLogger('mondiali')
gioco_bp = Blueprint('gioco', __name__)


def _utente_corrente(conn):
    if 'nome_utente' not in session:
        return None
    return db_fetchone(conn, 'SELECT * FROM utenti WHERE nome_utente=?',
                       (session['nome_utente'],))


# ─── Home ─────────────────────────────────────────────────────────────────────

@gioco_bp.route('/', endpoint='home')
def home():
    if 'nome_utente' not in session:
        return render_template('welcome.html', session=session)
    with db_conn() as conn:
        user = _utente_corrente(conn)
        if not user:
            return redirect(url_for('auth.logout'))
        uid = row_get(user, 'id')

        # Punteggio
        p = db_fetchone(conn, 'SELECT punteggio_totale FROM punteggi WHERE id_utente=?', (uid,))
        punteggio = row_get(p, 'punteggio_totale') or 0
        rank = db_fetchone(conn,
                           'SELECT COUNT(id)+1 AS r FROM punteggi WHERE punteggio_totale>?',
                           (punteggio,))
        posizione = row_get(rank, 'r') or 1

        # Stato giornata (gironi)
        g_row = db_fetchone(conn, 'SELECT giornata FROM stato_giornata WHERE is_attiva=TRUE')
        giornata_attiva = row_get(g_row, 'giornata') if g_row else None
        partite_gironi = []
        
        if giornata_attiva:
            partite_gironi = db_fetchall(conn,
                'SELECT * FROM partite WHERE giornata=? AND pronosticabile=TRUE ORDER BY data_ora_partita',
                (giornata_attiva,))
        if not partite_gironi:
            # Fallback: cerca partite pronosticabili senza risultato
            partite_gironi = db_fetchall(conn,
                'SELECT * FROM partite WHERE pronosticabile=TRUE '
                'AND risultato_casa_reale IS NULL '
                'ORDER BY data_ora_partita LIMIT 10')
            if partite_gironi and not giornata_attiva:
                giornata_attiva = row_get(partite_gironi[0], 'giornata')

        # Stato fase knockout
        f_row = db_fetchone(conn, 'SELECT * FROM stato_fase WHERE is_attiva=TRUE')
        fase_attiva = row_get(f_row, 'fase') if f_row else None
        partite_fase = []
        if fase_attiva:
            partite_fase = db_fetchall(conn,
                                       'SELECT * FROM partite WHERE fase=?',
                                       (fase_attiva,))

        # Pronostici torneo pubblici (visibili quando locked)
        lock_row = db_fetchone(conn,
            'SELECT is_locked FROM stato_pronostici_torneo WHERE id=1')
        torneo_locked = row_get(lock_row, 'is_locked') if lock_row else False
        pron_torneo = db_fetchone(conn,
            'SELECT * FROM pronostici_torneo WHERE id_utente=?', (uid,))
        pronostici_torneo_pubblici = []
        if torneo_locked:
            pronostici_torneo_pubblici = db_fetchall(conn,
                'SELECT u.nome_utente, pt.vincitore, pt.capocannoniere '
                'FROM utenti u LEFT JOIN pronostici_torneo pt ON u.id=pt.id_utente '
                'ORDER BY u.nome_utente')

        # Top 5 classifica
        top5 = db_fetchall(conn,
            'SELECT u.nome_utente, p.punteggio_totale FROM utenti u '
            'JOIN punteggi p ON u.id=p.id_utente '
            'ORDER BY p.punteggio_totale DESC LIMIT 5')

    return render_template('home.html',
                           punteggio=punteggio, posizione=posizione,
                           giornata_attiva=giornata_attiva,
                           partite_gironi=partite_gironi,
                           fase_attiva=fase_attiva,
                           partite_fase=partite_fase,
                           fase_nome=FASI_NOMI.get(fase_attiva, '') if fase_attiva else '',
                           torneo_locked=torneo_locked,
                           pron_torneo=pron_torneo,
                           pronostici_torneo_pubblici=pronostici_torneo_pubblici,
                           top5=top5, session=session)


# ─── Classifica ───────────────────────────────────────────────────────────────

@gioco_bp.route('/classifica', endpoint='classifica')
def classifica():
    if 'nome_utente' not in session:
        return redirect(url_for('auth.login'))
    with db_conn() as conn:
        classifica = db_fetchall(conn,
                                  'SELECT u.nome_utente, p.punteggio_totale FROM utenti u '
                                  'JOIN punteggi p ON u.id=p.id_utente '
                                  'ORDER BY p.punteggio_totale DESC')
        dettaglio = {}
        utenti = db_fetchall(conn, 'SELECT id, nome_utente FROM utenti')
        for u in utenti:
            uid = row_get(u, 'id')
            pg = {row_get(r, 'giornata'): row_get(r, 'punti')
                  for r in db_fetchall(conn,
                                       'SELECT giornata, punti FROM punteggi_giornata WHERE id_utente=?',
                                       (uid,))}
            pf = {row_get(r, 'fase'): row_get(r, 'punti')
                  for r in db_fetchall(conn,
                                       'SELECT fase, punti FROM punteggi_fase WHERE id_utente=?',
                                       (uid,))}
            dettaglio[row_get(u, 'nome_utente')] = {'giornate': pg, 'fasi': pf}

    return render_template('classifica.html',
                           classifica=classifica, dettaglio=dettaglio,
                           FASI_NOMI=FASI_NOMI, session=session)


# ─── Pronostici gironi ────────────────────────────────────────────────────────

@gioco_bp.route('/pronostici-gironi/<int:giornata>', methods=['GET', 'POST'],
                endpoint='pronostici_gironi')
def pronostici_gironi(giornata):
    if 'nome_utente' not in session:
        return redirect(url_for('auth.login'))
    with db_conn() as conn:
        user = _utente_corrente(conn)
        if not user:
            return redirect(url_for('auth.logout'))
        uid = row_get(user, 'id')

        partite = db_fetchall(conn,
                              'SELECT * FROM partite WHERE giornata=? AND pronosticabile=TRUE ORDER BY data_ora_partita',
                              (giornata,))

        tutti_giocatori = db_fetchall(conn,
            'SELECT nome_giocatore, squadra FROM giocatori '
            'ORDER BY squadra, nome_giocatore')
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
                pref_sc = sc[:4]
                pref_so = so[:4]
                lista = [g for s_key, gs in per_sq_db.items()
                         for g in gs
                         if (pref_sc and s_key.startswith(pref_sc)) or
                            (pref_so and s_key.startswith(pref_so))]
            if not lista:
                lista = tutti_giocatori
            giocatori_per_partita[pid] = lista

        pron_salvati = {row_get(r, 'id_partita'): r
                        for r in db_fetchall(conn,
                                             'SELECT * FROM pronostici_giornata WHERE id_utente=? '
                                             'AND id_partita IN (SELECT id FROM partite WHERE giornata=?)',
                                             (uid, giornata))}

        if request.method == 'POST':
            # --- FASE 1: VALIDAZIONE DEI DATI ---
            errori_validazione = False
            for p in partite:
                if is_partita_scaduta(row_get(p, 'data_ora_partita')):
                    continue
                pid   = row_get(p, 'id')
                esito = request.form.get(f'esito_{pid}')
                rcasa = request.form.get(f'casa_{pid}', '').strip()
                rosp  = request.form.get(f'ospite_{pid}', '').strip()
                marc  = (request.form.get(f'marcatore_{pid}') or '').strip()
                
                if esito or rcasa != '' or rosp != '' or marc:
                    if not esito or rcasa == '' or rosp == '' or not marc:
                        errori_validazione = True
                        break
            
            if errori_validazione:
                flash("Attenzione: hai compilato solo parzialmente una partita. Inserisci TUTTI i dati (Esito 1X2, Gol Casa, Gol Ospite, Marcatore)!", "warning")
                return redirect(url_for('gioco.pronostici_gironi', giornata=giornata))

            # --- FASE 2: SALVATAGGIO EFFETTIVO ---
            for p in partite:
                if is_partita_scaduta(row_get(p, 'data_ora_partita')):
                    continue
                pid   = row_get(p, 'id')
                esito = request.form.get(f'esito_{pid}')
                rcasa = _safe_int(request.form.get(f'casa_{pid}'), lo=0, hi=20)
                rosp  = _safe_int(request.form.get(f'ospite_{pid}'), lo=0, hi=20)
                marc  = (request.form.get(f'marcatore_{pid}') or '').strip()
                
                if esito and rcasa is not None and rosp is not None and marc:
                    if pid in pron_salvati:
                        db_execute(conn,
                                   'UPDATE pronostici_giornata SET esito_pronosticato=?, '
                                   'risultato_casa_pronosticato=?, risultato_ospite_pronosticato=?, '
                                   'marcatore_pronosticato=? WHERE id_utente=? AND id_partita=?',
                                   (esito, rcasa, rosp, marc, uid, pid))
                    else:
                        db_execute(conn,
                                   'INSERT INTO pronostici_giornata (id_utente, id_partita, '
                                   'esito_pronosticato, risultato_casa_pronosticato, '
                                   'risultato_ospite_pronosticato, marcatore_pronosticato) '
                                   'VALUES (?,?,?,?,?,?)',
                                   (uid, pid, esito, rcasa, rosp, marc))
            db_commit(conn)
            flash("Pronostici salvati con successo!", "success")
            return redirect(url_for('gioco.home'))

        scadenze = {row_get(p, 'id'): is_partita_scaduta(row_get(p, 'data_ora_partita'))
                    for p in partite}
        
        altri = {}
        punti_miei = {} 
        
        for p in partite:
            pid = row_get(p, 'id')
            pron = pron_salvati.get(pid)
            if pron and row_get(p, 'risultato_casa_reale') is not None:
                punti_miei[pid] = calcola_punti_pronostico(pron, p)['totale']
            else:
                punti_miei[pid] = None

            if scadenze.get(pid):
                rows = db_fetchall(conn,
                    'SELECT u.nome_utente, pg.* FROM pronostici_giornata pg '
                    'JOIN utenti u ON pg.id_utente=u.id WHERE pg.id_partita=?', (pid,))
                
                arricchiti = []
                for r in rows:
                    punti_tot = None
                    if row_get(p, 'risultato_casa_reale') is not None:
                        punti_tot = calcola_punti_pronostico(r, p)['totale']
                    
                    arricchiti.append({
                        'nome_utente': row_get(r, 'nome_utente'),
                        'esito_pronosticato': row_get(r, 'esito_pronosticato'),
                        'risultato_casa_pronosticato': row_get(r, 'risultato_casa_pronosticato'),
                        'risultato_ospite_pronosticato': row_get(r, 'risultato_ospite_pronosticato'),
                        'marcatore_pronosticato': row_get(r, 'marcatore_pronosticato'),
                        'punti_ottenuti': punti_tot
                    })
                altri[pid] = arricchiti

    return render_template('pronostici_gironi.html',
                           partite=partite, giornata=giornata,
                           pron_salvati=pron_salvati,
                           scadenze=scadenze, altri_pron=altri,
                           punti_miei=punti_miei,
                           giocatori_per_partita=giocatori_per_partita,
                           session=session)


# ─── Visualizza Giornata / Archivio ───────────────────────────────────────────

@gioco_bp.route('/giornata/<int:giornata>', endpoint='visualizza_giornata')
def visualizza_giornata(giornata):
    if 'nome_utente' not in session:
        return redirect(url_for('auth.login'))
    with db_conn() as conn:
        partite = db_fetchall(conn,
                              'SELECT * FROM partite WHERE giornata=? '
                              'AND pronosticabile=TRUE AND risultato_casa_reale IS NOT NULL ORDER BY data_ora_partita',
                              (giornata,))
        utenti  = db_fetchall(conn, 'SELECT id, nome_utente FROM utenti')
        pids    = [row_get(p, 'id') for p in partite]
        pron_idx = {}
        if pids:
            ph = ','.join(['?'] * len(pids))
            rows = db_fetchall(conn,
                               f'SELECT * FROM pronostici_giornata WHERE id_partita IN ({ph})',
                               tuple(pids))
            pron_idx = {(row_get(r, 'id_utente'), row_get(r, 'id_partita')): r for r in rows}

        classifica_g = []
        for u in utenti:
            uid = row_get(u, 'id')
            det = {}
            tot = 0
            for p in partite:
                pid = row_get(p, 'id')
                d   = calcola_punti_pronostico(pron_idx.get((uid, pid)), p)
                det[pid] = d
                tot += d['totale']
            classifica_g.append({'nome_utente': row_get(u, 'nome_utente'),
                                  'punti': tot, 'dettaglio': det})
        classifica_g.sort(key=lambda x: x['punti'], reverse=True)

    return render_template('visualizza_giornata.html',
                           giornata=giornata, partite=partite,
                           classifica=classifica_g, session=session)


@gioco_bp.route('/giornate', endpoint='archivio_giornate')
def archivio_giornate():
    if 'nome_utente' not in session:
        return redirect(url_for('auth.login'))
    with db_conn() as conn:
        giornate = db_fetchall(conn,
                               'SELECT * FROM stato_giornata WHERE is_in_archivio=TRUE ORDER BY giornata')
    return render_template('archivio_giornate.html', giornate=giornate, session=session)


@gioco_bp.route('/classifica-cumulativa/<int:giornata>', endpoint='classifica_cumulativa_giornata')
def classifica_cumulativa_giornata(giornata):
    """Ponte di emergenza: reindirizza alla classifica generale."""
    if 'nome_utente' not in session:
        return redirect(url_for('auth.login'))
    return redirect(url_for('gioco.classifica'))


# ─── Bracket eliminazione ─────────────────────────────────────────────────────

@gioco_bp.route('/bracket', endpoint='bracket')
def bracket():
    """Visualizzazione del bracket completo con pronostici dell'utente."""
    if 'nome_utente' not in session:
        return redirect(url_for('auth.login'))
    with db_conn() as conn:
        user = _utente_corrente(conn)
        if not user:
            return redirect(url_for('auth.logout'))
        uid = row_get(user, 'id')

        # Partite di ogni fase knockout
        fasi_partite = {}
        for fase in ['r32', 'r16', 'qf', 'sf', 'finale', '3posto']:
            fasi_partite[fase] = db_fetchall(conn,
                                             'SELECT * FROM partite WHERE fase=? ORDER BY data_ora_partita',
                                             (fase,))

        # Pronostici eliminazione dell'utente
        pron_elim = {row_get(r, 'id_partita'): r
                     for r in db_fetchall(conn,
                                         'SELECT * FROM pronostici_eliminazione WHERE id_utente=?',
                                         (uid,))}

        # Stato fasi
        stati_fase = {row_get(r, 'fase'): r
                      for r in db_fetchall(conn, 'SELECT * FROM stato_fase')}

    return render_template('bracket.html',
                           fasi_partite=fasi_partite,
                           pron_elim=pron_elim,
                           stati_fase=stati_fase,
                           FASI_NOMI=FASI_NOMI,
                           session=session)


@gioco_bp.route('/bracket/<fase>', methods=['GET', 'POST'],
                endpoint='pronostici_eliminazione')
def pronostici_eliminazione(fase):
    """Pronostici knockout — form gironi (esito+risultato+marcatore) con redirect alla Home."""
    if 'nome_utente' not in session:
        return redirect(url_for('auth.login'))
    with db_conn() as conn:
        user = _utente_corrente(conn)
        if not user:
            return redirect(url_for('auth.logout'))
        uid = row_get(user, 'id')

        stato     = db_fetchone(conn, 'SELECT * FROM stato_fase WHERE fase=?', (fase,))
        is_locked = row_get(stato, 'pronostici_locked') if stato else True

        partite = db_fetchall(conn,
                              'SELECT * FROM partite WHERE fase=? ORDER BY data_ora_partita',
                              (fase,))
        pron_salvati = {row_get(r, 'id_partita'): r
                        for r in db_fetchall(conn,
                                             'SELECT * FROM pronostici_eliminazione '
                                             'WHERE id_utente=? AND id_partita IN '
                                             '(SELECT id FROM partite WHERE fase=?)',
                                             (uid, fase))}

        # ── Giocatori con fallback garantito ────────────────────────────────
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
            if not lista:
                lista = tutti_giocatori
            giocatori_per_partita[pid] = lista

        if request.method == 'POST' and not is_locked:
            # --- FASE 1: VALIDAZIONE DEI DATI ---
            errori_validazione = False
            for p in partite:
                if is_partita_scaduta(row_get(p, 'data_ora_partita')):
                    continue
                pid    = row_get(p, 'id')
                esito  = request.form.get(f'esito_{pid}')
                rcasa  = request.form.get(f'casa_{pid}', '').strip()
                rosp   = request.form.get(f'ospite_{pid}', '').strip()
                marc   = (request.form.get(f'marcatore_{pid}') or '').strip()
                
                if esito or rcasa != '' or rosp != '' or marc:
                    if not esito or rcasa == '' or rosp == '' or not marc:
                        errori_validazione = True
                        break
            
            if errori_validazione:
                flash("Attenzione: hai compilato solo parzialmente una partita. Inserisci TUTTI i dati (Esito 1X2, Gol Casa, Gol Ospite, Marcatore)!", "warning")
                return redirect(url_for('gioco.pronostici_eliminazione', fase=fase))

            # --- FASE 2: SALVATAGGIO ---
            for p in partite:
                if is_partita_scaduta(row_get(p, 'data_ora_partita')):
                    continue
                pid    = row_get(p, 'id')
                esito  = request.form.get(f'esito_{pid}')
                r_casa = _safe_int(request.form.get(f'casa_{pid}'), lo=0, hi=20)
                r_osp  = _safe_int(request.form.get(f'ospite_{pid}'), lo=0, hi=20)
                marc   = (request.form.get(f'marcatore_{pid}') or '').strip()
                
                if esito and r_casa is not None and r_osp is not None and marc:
                    if pid in pron_salvati:
                        db_execute(conn,
                                   'UPDATE pronostici_eliminazione '
                                   'SET esito_pronosticato=?, '
                                   'risultato_casa_pronosticato=?, '
                                   'risultato_ospite_pronosticato=?, '
                                   'marcatore_pronosticato=? '
                                   'WHERE id_utente=? AND id_partita=?',
                                   (esito, r_casa, r_osp, marc, uid, pid))
                    else:
                        db_execute(conn,
                                   'INSERT INTO pronostici_eliminazione '
                                   '(id_utente, id_partita, esito_pronosticato, '
                                   'risultato_casa_pronosticato, '
                                   'risultato_ospite_pronosticato, '
                                   'marcatore_pronosticato) VALUES (?,?,?,?,?,?)',
                                   (uid, pid, esito, r_casa, r_osp, marc))
            db_commit(conn)
            flash("Pronostici salvati con successo!", "success")
            return redirect(url_for('auth.home'))

        scadenze = {row_get(p, 'id'): is_partita_scaduta(row_get(p, 'data_ora_partita'))
                    for p in partite}
        
        # Svelamento live dei pronostici se scaduta o bloccata
        tutti_pron = {}
        for p in partite:
            pid = row_get(p, 'id')
            if scadenze.get(pid) or is_locked:
                tutti_pron[pid] = db_fetchall(conn,
                    'SELECT u.nome_utente, pe.* FROM pronostici_eliminazione pe '
                    'JOIN utenti u ON pe.id_utente=u.id WHERE pe.id_partita=?', (pid,))

    return render_template('pronostici_eliminazione.html',
                           fase=fase, fase_nome=FASI_NOMI.get(fase, fase),
                           partite=partite,
                           pron_salvati=pron_salvati,
                           is_locked=is_locked,
                           scadenze=scadenze,
                           tutti_pron=tutti_pron,
                           giocatori_per_partita=giocatori_per_partita,
                           session=session)


# ─── Pronostici torneo ────────────────────────────────────────────────────────

@gioco_bp.route('/pronostici-torneo', methods=['GET', 'POST'],
                endpoint='pronostici_torneo')
def pronostici_torneo():
    """Inserimento pronostici torneo pre-partenza."""
    if 'nome_utente' not in session:
        return redirect(url_for('auth.login'))
    with db_conn() as conn:
        user = _utente_corrente(conn)
        if not user:
            return redirect(url_for('auth.logout'))
        uid = row_get(user, 'id')

        lock_row  = db_fetchone(conn, 'SELECT is_locked FROM stato_pronostici_torneo WHERE id=1')
        is_locked = row_get(lock_row, 'is_locked') if lock_row else True

        if is_locked:
            tutti = db_fetchall(conn,
                                'SELECT u.nome_utente, pt.* FROM utenti u '
                                'JOIN pronostici_torneo pt ON u.id=pt.id_utente '
                                'ORDER BY u.nome_utente')
            return render_template('pronostici_torneo.html',
                                   is_locked=True, tutti=tutti,
                                   PUNTI_TORNEO=PUNTI_TORNEO, session=session)

        if request.method == 'POST':
            vinc = (request.form.get('vincitore') or '').strip()
            fin  = (request.form.get('finalista') or '').strip()
            s1   = (request.form.get('semifinalista_1') or '').strip()
            s2   = (request.form.get('semifinalista_2') or '').strip()
            cc   = (request.form.get('capocannoniere') or '').strip()
            exists = db_fetchone(conn,
                                 'SELECT id FROM pronostici_torneo WHERE id_utente=?', (uid,))
            if exists:
                db_execute(conn,
                           'UPDATE pronostici_torneo SET vincitore=?, finalista=?, '
                           'semifinalista_1=?, semifinalista_2=?, capocannoniere=? '
                           'WHERE id_utente=?',
                           (vinc, fin, s1, s2, cc, uid))
            else:
                db_execute(conn,
                           'INSERT INTO pronostici_torneo '
                           '(id_utente, vincitore, finalista, semifinalista_1, '
                           'semifinalista_2, capocannoniere) VALUES (?,?,?,?,?,?)',
                           (uid, vinc, fin, s1, s2, cc))
            db_commit(conn)
            return redirect(url_for('gioco.home'))

        pron = db_fetchone(conn, 'SELECT * FROM pronostici_torneo WHERE id_utente=?', (uid,))
        squadre = [row_get(r, 'squadra_casa')
                   for r in db_fetchall(conn,
                                        'SELECT DISTINCT squadra_casa FROM partite ORDER BY squadra_casa')]

    return render_template('pronostici_torneo.html',
                           is_locked=False, pron=pron, squadre=squadre,
                           PUNTI_TORNEO=PUNTI_TORNEO, session=session)
