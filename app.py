import sys
import os
from flask import Flask, render_template, request, redirect, url_for, session, flash
import sqlite3
import hashlib
import random
import string
from datetime import datetime, timedelta
import pytz

app = Flask(__name__)
app.secret_key = 'chiave_segreta_molto_segreta'

app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

@app.context_processor
def inject_giornata_attiva():
    """Rende giornata_attiva disponibile in tutti i template (serve alla tab bar)."""
    try:
        conn = get_db_connection()
        row = conn.execute("SELECT giornata FROM stato_giornata WHERE is_attiva = 1").fetchone()
        conn.close()
        return {'giornata_attiva': row['giornata'] if row else None}
    except Exception:
        return {'giornata_attiva': None}

def parse_flexible_datetime(date_string):
    """
    Interpreta una stringa di data e ora che può avere o non avere i secondi.
    """
    if not date_string:
        return None
    try:
        return datetime.strptime(date_string, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return datetime.strptime(date_string, "%Y-%m-%dT%H:%M")

def get_db_connection():
    conn = sqlite3.connect(os.path.join(app.root_path, 'database.db'), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

@app.template_filter('fuso_orario_italia')
def fuso_orario_italia(data_ora_utc_str):
    if not data_ora_utc_str: return 'Non impostato'
    try:
        roma_timezone = pytz.timezone('Europe/Rome')
        utc_timezone = pytz.utc
        orario_naive = parse_flexible_datetime(data_ora_utc_str)
        if orario_naive is None:
            return data_ora_utc_str
        orario_utc_aware = utc_timezone.localize(orario_naive)
        orario_romano = orario_utc_aware.astimezone(roma_timezone)
        return orario_romano.strftime("%d/%m/%Y alle %H:%M")
    except (ValueError, TypeError):
        return data_ora_utc_str

def create_tables():
    conn = get_db_connection()
    conn.execute("CREATE TABLE IF NOT EXISTS utenti (id INTEGER PRIMARY KEY AUTOINCREMENT, nome_utente TEXT NOT NULL UNIQUE, password TEXT NOT NULL, is_temp_password BOOLEAN NOT NULL DEFAULT 0);")
    conn.execute("CREATE TABLE IF NOT EXISTS pronostici_iniziali (id INTEGER PRIMARY KEY AUTOINCREMENT, id_utente INTEGER NOT NULL, squadra_1 TEXT, squadra_2 TEXT, squadra_3 TEXT, squadra_4 TEXT, capocannoniere TEXT, FOREIGN KEY(id_utente) REFERENCES utenti(id));")
    conn.execute("CREATE TABLE IF NOT EXISTS pronostici_giornata (id INTEGER PRIMARY KEY AUTOINCREMENT, id_utente INTEGER NOT NULL, id_partita INTEGER NOT NULL, esito_pronosticato TEXT, risultato_casa_pronosticato INTEGER, risultato_ospite_pronosticato INTEGER, marcatore_pronosticato TEXT, FOREIGN KEY(id_utente) REFERENCES utenti(id), FOREIGN KEY(id_partita) REFERENCES partite(id));")
    conn.execute("CREATE TABLE IF NOT EXISTS partite (id INTEGER PRIMARY KEY AUTOINCREMENT, giornata INTEGER NOT NULL, squadra_casa TEXT NOT NULL, squadra_ospite TEXT NOT NULL, risultato_casa_reale INTEGER, risultato_ospite_reale INTEGER, marcatore_reale TEXT, pronosticabile BOOLEAN NOT NULL DEFAULT 0, data_ora_partita TEXT);")
    conn.execute("CREATE TABLE IF NOT EXISTS punteggi (id INTEGER PRIMARY KEY AUTOINCREMENT, id_utente INTEGER NOT NULL UNIQUE, punteggio_totale INTEGER NOT NULL DEFAULT 0, FOREIGN KEY(id_utente) REFERENCES utenti(id));")
    conn.execute("CREATE TABLE IF NOT EXISTS stato_giornata (id INTEGER PRIMARY KEY AUTOINCREMENT, giornata INTEGER NOT NULL UNIQUE, is_attiva BOOLEAN NOT NULL DEFAULT 0, is_in_archivio BOOLEAN NOT NULL DEFAULT 0);")
    conn.execute("CREATE TABLE IF NOT EXISTS stato_pronostici_iniziali (id INTEGER PRIMARY KEY, is_locked BOOLEAN NOT NULL DEFAULT 0);")
    conn.execute("INSERT OR IGNORE INTO stato_pronostici_iniziali (id, is_locked) VALUES (1, 0)")
    conn.execute("CREATE TABLE IF NOT EXISTS risultati_finali (id INTEGER PRIMARY KEY, squadra_1 TEXT, squadra_2 TEXT, squadra_3 TEXT, squadra_4 TEXT, capocannoniere TEXT);")
    conn.execute("INSERT OR IGNORE INTO risultati_finali (id) VALUES (1)")
    conn.execute("CREATE TABLE IF NOT EXISTS giocatori (id INTEGER PRIMARY KEY AUTOINCREMENT, nome_giocatore TEXT NOT NULL, squadra TEXT NOT NULL);")
    if conn.execute("SELECT COUNT(*) FROM partite").fetchone()[0] == 0:
        partite_da_inserire = [ (1, 'INTER', 'LECCE', None, None, None, True, '2025-08-18T18:45'), (1, 'FROSINONE', 'BOLOGNA', None, None, None, True, '2025-08-18T16:30'), (1, 'LAZIO', 'EMPOLI', None, None, None, True, '2025-08-19T18:45'), (1, 'JUVENTUS', 'AC MILAN', None, None, None, False, '2025-08-19T18:45'), ]
        conn.executemany("INSERT INTO partite (giornata, squadra_casa, squadra_ospite, risultato_casa_reale, risultato_ospite_reale, marcatore_reale, pronosticabile, data_ora_partita) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", partite_da_inserire)
    if conn.execute("SELECT COUNT(*) FROM stato_giornata").fetchone()[0] == 0:
        conn.execute("INSERT INTO stato_giornata (giornata, is_attiva) VALUES (1, 1)")
    conn.commit()
    conn.close()

@app.route("/")
def home():
    if 'nome_utente' not in session: return render_template("welcome.html", session=session)
    conn = get_db_connection()
    giornata_attiva_row = conn.execute("SELECT giornata FROM stato_giornata WHERE is_attiva = 1").fetchone()
    giornata_attiva = giornata_attiva_row['giornata'] if giornata_attiva_row else None
    user = conn.execute("SELECT id FROM utenti WHERE nome_utente = ?", (session['nome_utente'],)).fetchone()
    if not user: return redirect(url_for('logout'))
    user_id = user['id']
    punteggio_row = conn.execute("SELECT punteggio_totale FROM punteggi WHERE id_utente = ?", (user_id,)).fetchone()
    punteggio_utente = punteggio_row['punteggio_totale'] if punteggio_row else 0
    posizione_row = conn.execute("SELECT COUNT(id) + 1 as rank FROM punteggi WHERE punteggio_totale > ?", (punteggio_utente,)).fetchone()
    posizione_utente = posizione_row['rank'] if posizione_row else 1
    conn.close()
    return render_template("home.html", giornata_attiva=giornata_attiva, punteggio_utente=punteggio_utente, posizione_utente=posizione_utente, session=session)

@app.route("/registrazione", methods=["GET", "POST"])
def registrazione():
    if request.method == "POST":
        nome_utente, password = request.form["nome_utente"], request.form["password"]
        password_hash = hashlib.sha256(password.encode()).hexdigest()
        try:
            conn = get_db_connection()
            conn.execute("INSERT INTO utenti (nome_utente, password) VALUES (?, ?)", (nome_utente, password_hash))
            conn.commit()
            conn.close()
            return redirect(url_for("home"))
        except sqlite3.IntegrityError:
            return render_template("registrazione.html", session=session, errore="Nome utente già esistente. Scegli un altro nome.")
    return render_template("registrazione.html", session=session)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        nome_utente, password = request.form["nome_utente"], request.form["password"]
        password_hash = hashlib.sha256(password.encode()).hexdigest()
        conn = get_db_connection()
        user = conn.execute("SELECT * FROM utenti WHERE nome_utente = ? AND password = ?", (nome_utente, password_hash)).fetchone()
        conn.close()
        if user:
            if request.form.get('remember'):
                session.permanent = True
            session['nome_utente'] = nome_utente
            if user['is_temp_password']:
                return redirect(url_for('cambia_password'))
            return redirect(url_for("home"))
        else:
            return render_template("login.html", session=session, errore="Credenziali non valide. Riprova.")
    return render_template("login.html", session=session)

@app.route("/cambia-password", methods=["GET", "POST"])
def cambia_password():
    if 'nome_utente' not in session: return redirect(url_for('login'))
    if request.method == 'POST':
        nuova_password, conferma_password = request.form['nuova_password'], request.form['conferma_password']
        if nuova_password == conferma_password:
            nuova_password_hash = hashlib.sha256(nuova_password.encode()).hexdigest()
            conn = get_db_connection()
            conn.execute("UPDATE utenti SET password = ?, is_temp_password = 0 WHERE nome_utente = ?", (nuova_password_hash, session['nome_utente']))
            conn.commit()
            conn.close()
            return redirect(url_for('home'))
        else:
            return render_template('cambia_password.html', session=session, errore="Le password non coincidono.")
    return render_template('cambia_password.html', session=session)

@app.route("/logout")
def logout():
    session.pop('nome_utente', None)
    return redirect(url_for("home"))

@app.route("/pronostici-iniziali", methods=["GET", "POST"])
def pronostici_iniziali():
    if 'nome_utente' not in session:
        return redirect(url_for("login"))

    conn = get_db_connection()
    try:
        lock_status = conn.execute("SELECT is_locked FROM stato_pronostici_iniziali WHERE id = 1").fetchone()
        is_locked = lock_status['is_locked'] if lock_status else True

        user = conn.execute("SELECT id FROM utenti WHERE nome_utente = ?", (session['nome_utente'],)).fetchone()
        if not user:
            return redirect(url_for('logout'))
        user_id = user['id']

        # --- INIZIO MODIFICA ---
        if is_locked:
            # Se i pronostici sono bloccati, recuperiamo quelli di tutti gli utenti
            pronostici_tutti = conn.execute("""
                SELECT u.nome_utente, pi.* FROM pronostici_iniziali pi
                JOIN utenti u ON pi.id_utente = u.id
                ORDER BY u.nome_utente
            """).fetchall()
            return render_template("pronostici_iniziali.html", is_locked=is_locked, pronostici_tutti=pronostici_tutti, session=session)
        # --- FINE MODIFICA ---

        # Se non sono bloccati, la logica rimane la stessa di prima
        if request.method == "POST":
            squadra_1 = request.form["squadra_1"]
            squadra_2 = request.form["squadra_2"]
            squadra_3 = request.form["squadra_3"]
            squadra_4 = request.form["squadra_4"]
            capocannoniere = request.form["capocannoniere"]

            pronostico_esistente = conn.execute("SELECT id FROM pronostici_iniziali WHERE id_utente = ?", (user_id,)).fetchone()

            if pronostico_esistente:
                conn.execute("UPDATE pronostici_iniziali SET squadra_1 = ?, squadra_2 = ?, squadra_3 = ?, squadra_4 = ?, capocannoniere = ? WHERE id_utente = ?",
                             (squadra_1, squadra_2, squadra_3, squadra_4, capocannoniere, user_id))
            else:
                conn.execute("INSERT INTO pronostici_iniziali (id_utente, squadra_1, squadra_2, squadra_3, squadra_4, capocannoniere) VALUES (?, ?, ?, ?, ?, ?)",
                             (user_id, squadra_1, squadra_2, squadra_3, squadra_4, capocannoniere))

            conn.commit()
            return redirect(url_for("home"))

        pronostico_utente = conn.execute("SELECT * FROM pronostici_iniziali WHERE id_utente = ?", (user_id,)).fetchone()
        return render_template("pronostici_iniziali.html", is_locked=is_locked, pronostico=pronostico_utente, session=session)

    finally:
        conn.close()

@app.route("/pronostici-giornata/<int:giornata>", methods=["GET", "POST"])
def pronostici_giornata(giornata):
    if 'nome_utente' not in session: return redirect(url_for("login"))
    conn = get_db_connection()
    user_id = conn.execute("SELECT id FROM utenti WHERE nome_utente = ?", (session['nome_utente'],)).fetchone()['id']
    partite = conn.execute("SELECT * FROM partite WHERE giornata = ? AND pronosticabile = 1", (giornata,)).fetchall()
    giocatori_per_partita = {}
    for partita in partite:
        squadra_casa = partita['squadra_casa'].upper()
        squadra_ospite = partita['squadra_ospite'].upper()
        giocatori = conn.execute("SELECT nome_giocatore, squadra FROM giocatori WHERE squadra = ? OR squadra = ? ORDER BY squadra, nome_giocatore", (squadra_casa, squadra_ospite)).fetchall()
        giocatori_per_partita[partita['id']] = giocatori
    roma_timezone = pytz.timezone('Europe/Rome')
    ora_corrente_roma = datetime.now(roma_timezone)
    pronostici_salvati = conn.execute("SELECT * FROM pronostici_giornata WHERE id_utente = ? AND id_partita IN (SELECT id FROM partite WHERE giornata = ?)", (user_id, giornata)).fetchall()
    pronostici_dict = {p['id_partita']: p for p in pronostici_salvati}
    pronostici_altri_utenti = {}
    scadenze_dict = {}
    if request.method == "POST":
        for partita in partite:
            is_scaduta = False
            if partita['data_ora_partita']:
                orario_naive = parse_flexible_datetime(partita['data_ora_partita'])
                if orario_naive:
                    orario_utc_aware = pytz.utc.localize(orario_naive)
                    orario_partita_local = orario_utc_aware.astimezone(roma_timezone)
                    if ora_corrente_roma > orario_partita_local:
                        is_scaduta = True
            if not is_scaduta:
                esito = request.form.get(f"esito_{partita['id']}")
                r_casa = request.form.get(f"risultato_casa_{partita['id']}")
                r_ospite = request.form.get(f"risultato_ospite_{partita['id']}")
                marcatore = request.form.get(f"marcatore_{partita['id']}")
                if esito or (r_casa and r_ospite) or marcatore:
                    if partita['id'] in pronostici_dict:
                        conn.execute("UPDATE pronostici_giornata SET esito_pronosticato = ?, risultato_casa_pronosticato = ?, risultato_ospite_pronosticato = ?, marcatore_pronosticato = ? WHERE id_utente = ? AND id_partita = ?", (esito, r_casa, r_ospite, marcatore, user_id, partita['id']))
                    else:
                        conn.execute("INSERT INTO pronostici_giornata (id_utente, id_partita, esito_pronosticato, risultato_casa_pronosticato, risultato_ospite_pronosticato, marcatore_pronosticato) VALUES (?, ?, ?, ?, ?, ?)", (user_id, partita['id'], esito, r_casa, r_ospite, marcatore))
        conn.commit()
        conn.close()
        return redirect(url_for("home"))
    for partita in partite:
        is_scaduta = False
        if partita['data_ora_partita']:
            orario_naive = parse_flexible_datetime(partita['data_ora_partita'])
            if orario_naive:
                orario_utc_aware = pytz.utc.localize(orario_naive)
                orario_partita_local = orario_utc_aware.astimezone(roma_timezone)
                if ora_corrente_roma > orario_partita_local:
                    is_scaduta = True
        scadenze_dict[partita['id']] = is_scaduta
        if is_scaduta:
            pronostici_partita = conn.execute("SELECT u.nome_utente, pg.* FROM pronostici_giornata pg JOIN utenti u ON pg.id_utente = u.id WHERE pg.id_partita = ?", (partita['id'],)).fetchall()
            pronostici_altri_utenti[partita['id']] = pronostici_partita
    conn.close()
    return render_template("pronostici_giornata.html", partite=partite, giornata=giornata, pronostici_per_partita=pronostici_dict, scadenze=scadenze_dict, pronostici_altri_utenti=pronostici_altri_utenti, giocatori_per_partita=giocatori_per_partita, session=session)

@app.route("/admin")
def admin_home():
    if 'nome_utente' not in session or session['nome_utente'] != 'mirko': return "Accesso negato.", 403
    conn = get_db_connection()
    giornata_attiva = conn.execute("SELECT giornata FROM stato_giornata WHERE is_attiva = 1").fetchone()
    giornate_archiviate = conn.execute("SELECT * FROM stato_giornata WHERE is_in_archivio = 1 ORDER BY giornata DESC").fetchall()
    partite_giornata = []
    giocatori_per_partita = {}
    if giornata_attiva:
        partite_giornata = conn.execute("SELECT * FROM partite WHERE giornata = ? AND pronosticabile = 1", (giornata_attiva['giornata'],)).fetchall()
        for partita in partite_giornata:
            squadra_casa = partita['squadra_casa'].upper()
            squadra_ospite = partita['squadra_ospite'].upper()
            giocatori = conn.execute("SELECT nome_giocatore, squadra FROM giocatori WHERE squadra = ? OR squadra = ? ORDER BY squadra, nome_giocatore", (squadra_casa, squadra_ospite)).fetchall()
            giocatori_per_partita[partita['id']] = giocatori
    flash_message = session.pop('flash_message', None)
    conn.close()
    return render_template("admin.html", partite=partite_giornata, giornata=giornata_attiva['giornata'] if giornata_attiva else None, giornate_archiviate=giornate_archiviate, flash_message=flash_message, giocatori_per_partita=giocatori_per_partita, session=session)

@app.route("/admin/utenti")
def admin_utenti():
    if 'nome_utente' not in session or session['nome_utente'] != 'mirko': return "Accesso negato.", 403
    conn = get_db_connection()
    utenti = conn.execute("SELECT id, nome_utente, is_temp_password FROM utenti").fetchall()
    conn.close()
    return render_template("admin_utenti.html", utenti=utenti, session=session)

@app.route("/admin/resetta-password/<int:id_utente>")
def admin_resetta_password(id_utente):
    if 'nome_utente' not in session or session['nome_utente'] != 'mirko': return "Accesso negato.", 403
    password_temporanea = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    password_hash = hashlib.sha256(password_temporanea.encode()).hexdigest()
    conn = get_db_connection()
    conn.execute("UPDATE utenti SET password = ?, is_temp_password = 1 WHERE id = ?", (password_hash, id_utente))
    conn.commit()
    utente = conn.execute("SELECT nome_utente FROM utenti WHERE id = ?", (id_utente,)).fetchone()
    conn.close()
    if utente:
        return f"La nuova password temporanea per {utente['nome_utente']} è: **{password_temporanea}**<br><a href='/admin/utenti'>Torna all'elenco utenti</a>"
    return "Utente non trovato."

@app.route("/admin/elimina-utente/<int:id_utente>")
def admin_elimina_utente(id_utente):
    if 'nome_utente' not in session or session['nome_utente'] != 'mirko': return "Accesso negato.", 403
    conn = get_db_connection()
    utente_da_eliminare = conn.execute("SELECT nome_utente FROM utenti WHERE id = ?", (id_utente,)).fetchone()
    if utente_da_eliminare and utente_da_eliminare['nome_utente'] == 'mirko':
        conn.close()
        return "Impossibile eliminare l'amministratore principale. <a href='/admin/utenti'>Torna indietro</a>"
    conn.execute("DELETE FROM punteggi WHERE id_utente = ?", (id_utente,))
    conn.execute("DELETE FROM pronostici_giornata WHERE id_utente = ?", (id_utente,))
    conn.execute("DELETE FROM pronostici_iniziali WHERE id_utente = ?", (id_utente,))
    conn.execute("DELETE FROM utenti WHERE id = ?", (id_utente,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_utenti'))

@app.route("/admin/gestisci-pronostici-iniziali")
def admin_gestisci_pronostici_iniziali():
    if 'nome_utente' not in session or session['nome_utente'] != 'mirko': return "Accesso negato.", 403
    conn = get_db_connection()
    pronostici_iniziali = conn.execute("SELECT u.nome_utente, pi.* FROM utenti u JOIN pronostici_iniziali pi ON u.id = pi.id_utente").fetchall()
    conn.close()
    return render_template("admin_gestisci_pronostici_iniziali.html", pronostici=pronostici_iniziali, session=session)

@app.route("/admin/modifica-pronostico-iniziale/<int:id_pronostico>", methods=["POST"])
def admin_modifica_pronostico_iniziale(id_pronostico):
    if 'nome_utente' not in session or session['nome_utente'] != 'mirko': return "Accesso negato.", 403
    squadra_1, squadra_2, squadra_3, squadra_4, capocannoniere = request.form["squadra_1"], request.form["squadra_2"], request.form["squadra_3"], request.form["squadra_4"], request.form["capocannoniere"]
    conn = get_db_connection()
    conn.execute("UPDATE pronostici_iniziali SET squadra_1 = ?, squadra_2 = ?, squadra_3 = ?, squadra_4 = ?, capocannoniere = ? WHERE id = ?", (squadra_1, squadra_2, squadra_3, squadra_4, capocannoniere, id_pronostico))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_gestisci_pronostici_iniziali'))

@app.route("/admin/elimina-pronostico-iniziale/<int:id_pronostico>")
def admin_elimina_pronostico_iniziale(id_pronostico):
    if 'nome_utente' not in session or session['nome_utente'] != 'mirko': return "Accesso negato.", 403
    conn = get_db_connection()
    conn.execute("DELETE FROM pronostici_iniziali WHERE id = ?", (id_pronostico,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_gestisci_pronostici_iniziali'))

@app.route("/admin/risultati-giornata/<int:giornata>", methods=["POST"])
def admin_risultati_giornata(giornata):
    if 'nome_utente' not in session or session['nome_utente'] != 'mirko': return "Accesso negato.", 403
    conn = get_db_connection()
    partite = conn.execute("SELECT * FROM partite WHERE giornata = ? AND pronosticabile = 1", (giornata,)).fetchall()
    for partita in partite:
        risultato_casa = request.form.get(f"risultato_casa_{partita['id']}")
        risultato_ospite = request.form.get(f"risultato_ospite_{partita['id']}")
        marcatori_selezionati = request.form.getlist(f"marcatore_{partita['id']}")
        marcatori_str = ",".join(marcatori_selezionati)
        conn.execute("UPDATE partite SET risultato_casa_reale = ?, risultato_ospite_reale = ?, marcatore_reale = ? WHERE id = ?", (risultato_casa, risultato_ospite, marcatori_str, partita['id']))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_home"))

@app.route("/admin/gestisci-partite")
def admin_gestisci_partite():
    if 'nome_utente' not in session or session['nome_utente'] != 'mirko': return "Accesso negato.", 403
    conn = get_db_connection()
    giornata_selezionata = request.args.get('giornata', type=int)
    giornate_disponibili_rows = conn.execute("SELECT DISTINCT giornata FROM partite ORDER BY giornata").fetchall()
    giornate_disponibili = [row['giornata'] for row in giornate_disponibili_rows]
    if giornata_selezionata:
        partite_da_mostrare = conn.execute("SELECT * FROM partite WHERE giornata = ? ORDER BY data_ora_partita", (giornata_selezionata,)).fetchall()
    else:
        partite_da_mostrare = conn.execute("SELECT * FROM partite ORDER BY giornata, data_ora_partita").fetchall()
    conn.close()
    return render_template("admin_gestisci_partite.html", tutte_le_partite=partite_da_mostrare, giornate_disponibili=giornate_disponibili, giornata_selezionata=giornata_selezionata, session=session)

@app.route("/admin/aggiungi-partita", methods=["POST"])
def aggiungi_partita():
    if 'nome_utente' not in session or session['nome_utente'] != 'mirko': return "Accesso negato.", 403
    giornata, squadra_casa, squadra_ospite, pronosticabile, data_ora_partita = request.form["giornata"], request.form["squadra_casa"].upper(), request.form["squadra_ospite"].upper(), request.form.get("pronosticabile") == "on", request.form["data_ora_partita"]
    conn = get_db_connection()
    conn.execute("INSERT INTO partite (giornata, squadra_casa, squadra_ospite, pronosticabile, data_ora_partita) VALUES (?, ?, ?, ?, ?)", (giornata, squadra_casa, squadra_ospite, pronosticabile, data_ora_partita))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_gestisci_partite"))

@app.route("/admin/modifica-partita/<int:id_partita>", methods=["POST"])
def admin_modifica_partita(id_partita):
    if 'nome_utente' not in session or session['nome_utente'] != 'mirko': return "Accesso negato.", 403
    giornata, squadra_casa, squadra_ospite, pronosticabile, data_ora_partita = request.form["giornata"], request.form["squadra_casa"].upper(), request.form["squadra_ospite"].upper(), request.form.get("pronosticabile") == "on", request.form["data_ora_partita"]
    conn = get_db_connection()
    conn.execute("UPDATE partite SET giornata = ?, squadra_casa = ?, squadra_ospite = ?, pronosticabile = ?, data_ora_partita = ? WHERE id = ?", (giornata, squadra_casa, squadra_ospite, pronosticabile, data_ora_partita, id_partita))
    conn.commit()
    conn.close()
    giornata_selezionata = request.args.get('giornata')
    if giornata_selezionata: return redirect(url_for("admin_gestisci_partite", giornata=giornata_selezionata))
    return redirect(url_for("admin_gestisci_partite"))
@app.route("/admin/salva-modifiche-giornata", methods=["POST"])
def admin_salva_modifiche_giornata():
    if 'nome_utente' not in session or session['nome_utente'] != 'mirko': return "Accesso negato.", 403
    conn = get_db_connection()

    # Recuperiamo la lista di tutti gli ID delle partite che erano a video
    ids_partite = request.form.getlist('partita_ids')
    giornata_redirect = request.form.get('giornata_corrente') # Per ricaricare la pagina giusta

    for id_partita in ids_partite:
        # Per ogni ID, andiamo a cercare i dati corrispondenti nel form
        # I nomi dei campi ora hanno l'ID come suffisso (es: squadra_casa_15)
        giornata = request.form.get(f"giornata_{id_partita}")
        squadra_casa = request.form.get(f"squadra_casa_{id_partita}").upper()
        squadra_ospite = request.form.get(f"squadra_ospite_{id_partita}").upper()
        data_ora = request.form.get(f"data_ora_partita_{id_partita}")

        # Checkbox: se è presente nel form vuol dire che è checkato, altrimenti no
        pronosticabile = True if request.form.get(f"pronosticabile_{id_partita}") else False

        conn.execute("""
            UPDATE partite
            SET giornata = ?, squadra_casa = ?, squadra_ospite = ?, pronosticabile = ?, data_ora_partita = ?
            WHERE id = ?
        """, (giornata, squadra_casa, squadra_ospite, pronosticabile, data_ora, id_partita))

    conn.commit()
    conn.close()

    if giornata_redirect:
         return redirect(url_for("admin_gestisci_partite", giornata=giornata_redirect))
    return redirect(url_for("admin_gestisci_partite"))
@app.route("/admin/elimina-partita/<int:id_partita>")
def admin_elimina_partita(id_partita):
    if 'nome_utente' not in session or session['nome_utente'] != 'mirko': return "Accesso negato.", 403
    conn = get_db_connection()
    conn.execute("DELETE FROM partite WHERE id = ?", (id_partita,))
    conn.commit()
    conn.close()
    giornata_selezionata = request.args.get('giornata')
    if giornata_selezionata: return redirect(url_for("admin_gestisci_partite", giornata=giornata_selezionata))
    return redirect(url_for("admin_gestisci_partite"))

@app.route("/admin/archivia-giornata/<int:giornata>")
def archivia_giornata(giornata):
    if 'nome_utente' not in session or session['nome_utente'] != 'mirko': return "Accesso negato.", 403
    conn = get_db_connection()
    conn.execute("UPDATE stato_giornata SET is_attiva = 0, is_in_archivio = 1 WHERE giornata = ?", (giornata,))
    prossima_giornata = giornata + 1
    conn.execute("INSERT OR IGNORE INTO stato_giornata (giornata, is_attiva) VALUES (?, 1)", (prossima_giornata,))
    conn.execute("UPDATE stato_giornata SET is_attiva = 1 WHERE giornata = ?", (prossima_giornata,))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_home"))

@app.route("/admin/disarchivia-giornata/<int:giornata>")
def disarchivia_giornata(giornata):
    if 'nome_utente' not in session or session['nome_utente'] != 'mirko': return "Accesso negato.", 403
    conn = get_db_connection()
    conn.execute("UPDATE stato_giornata SET is_in_archivio = 0 WHERE giornata = ?", (giornata,))
    giornata_attiva = conn.execute("SELECT giornata FROM stato_giornata WHERE is_attiva = 1").fetchone()
    if giornata_attiva:
        conn.execute("UPDATE stato_giornata SET is_attiva = 0 WHERE giornata = ?", (giornata_attiva['giornata'],))
    conn.execute("UPDATE stato_giornata SET is_attiva = 1 WHERE giornata = ?", (giornata,))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_home"))

def calcola_e_aggiorna_punti_giornata(giornata):
    conn = get_db_connection()
    utenti = conn.execute("SELECT id FROM utenti").fetchall()
    partite_calcolo = conn.execute("SELECT * FROM partite WHERE giornata = ? AND pronosticabile = 1 AND risultato_casa_reale IS NOT NULL", (giornata,)).fetchall()
    if not partite_calcolo:
        conn.close()
        return f"Nessuna partita con risultati trovata per la giornata {giornata}. Impossibile calcolare."
    for utente in utenti:
        punti_giornata = 0
        for partita in partite_calcolo:
            pronostico = conn.execute("SELECT * FROM pronostici_giornata WHERE id_utente = ? AND id_partita = ?", (utente['id'], partita['id'])).fetchone()
            if pronostico:
                punti_partita = 0; esito_corretto = False; risultato_corretto = False; marcatore_corretto = False
                esito_reale = "1" if partita['risultato_casa_reale'] > partita['risultato_ospite_reale'] else "X" if partita['risultato_casa_reale'] == partita['risultato_ospite_reale'] else "2"
                if pronostico['esito_pronosticato'] == esito_reale: punti_partita += 1; esito_corretto = True
                if pronostico['risultato_casa_pronosticato'] == partita['risultato_casa_reale'] and pronostico['risultato_ospite_pronosticato'] == partita['risultato_ospite_reale']: punti_partita += 3; risultato_corretto = True
                pronostico_marcatore = pronostico['marcatore_pronosticato'].strip().lower() if pronostico['marcatore_pronosticato'] else ""
                marcatori_reali = [m.strip().lower() for m in partita['marcatore_reale'].split(',')] if partita['marcatore_reale'] else []
                if pronostico_marcatore == "nessun marcatore":
                    if partita['risultato_casa_reale'] == 0 and partita['risultato_ospite_reale'] == 0:
                        punti_partita += 2; marcatore_corretto = True
                elif pronostico_marcatore and pronostico_marcatore in marcatori_reali:
                    punti_partita += 2; marcatore_corretto = True
                if esito_corretto and risultato_corretto and marcatore_corretto: punti_partita += 1
                punti_giornata += punti_partita
        conn.execute("INSERT INTO punteggi (id_utente, punteggio_totale) VALUES (?, 0) ON CONFLICT(id_utente) DO NOTHING", (utente['id'],))
        conn.execute("UPDATE punteggi SET punteggio_totale = punteggio_totale + ? WHERE id_utente = ?", (punti_giornata, utente['id']))
    conn.commit()
    conn.close()
    return f"Punti per la Giornata {giornata} calcolati e aggiunti alla classifica generale!"

@app.route("/admin/calcola-punti-giornata/<int:giornata>")
def admin_calcola_punti_giornata(giornata):
    if 'nome_utente' not in session or session['nome_utente'] != 'mirko': return "Accesso negato.", 403
    messaggio = calcola_e_aggiorna_punti_giornata(giornata)
    session['flash_message'] = messaggio
    return redirect(url_for("admin_home"))

def ricalcola_punteggi_totali():
    conn = get_db_connection()
    conn.execute("DELETE FROM punteggi")
    utenti = conn.execute("SELECT id FROM utenti").fetchall()
    for utente in utenti:
        conn.execute("INSERT INTO punteggi (id_utente, punteggio_totale) VALUES (?, 0)", (utente['id'],))
    conn.commit()
    giornate_calcolo = conn.execute("SELECT giornata FROM stato_giornata WHERE is_in_archivio = 1").fetchall()
    for giornata_stato in giornate_calcolo:
        calcola_e_aggiorna_punti_giornata(giornata_stato['giornata'])
    conn.close()
    return "Classifica generale ricalcolata con successo basandosi su tutte le giornate archiviate."

@app.route("/calcola-punteggi")
def calcola_punteggi():
    if 'nome_utente' not in session or session['nome_utente'] != 'mirko': return "Accesso negato.", 403
    messaggio = ricalcola_punteggi_totali()
    session['flash_message'] = messaggio
    return redirect(url_for("admin_home"))

@app.route("/classifica")
def classifica():
    if 'nome_utente' not in session: return redirect(url_for("login"))
    conn = get_db_connection()
    classifica_utenti = conn.execute("SELECT u.nome_utente, p.punteggio_totale FROM utenti u JOIN punteggi p ON u.id = p.id_utente ORDER BY p.punteggio_totale DESC").fetchall()
    conn.close()
    return render_template("classifica.html", classifica=classifica_utenti, session=session)

@app.route("/giornate")
def archivio_giornate():
    if 'nome_utente' not in session: return redirect(url_for("login"))
    conn = get_db_connection()
    giornate = conn.execute("SELECT * FROM stato_giornata WHERE is_in_archivio = 1 ORDER BY giornata").fetchall()
    conn.close()
    return render_template("archivio_giornate.html", giornate=giornate, session=session)

@app.route("/giornata/<int:giornata>")
def visualizza_giornata(giornata):
    if 'nome_utente' not in session: return redirect(url_for("login"))
    conn = get_db_connection()
    partite_reali = conn.execute("SELECT * FROM partite WHERE giornata = ? AND risultato_casa_reale IS NOT NULL", (giornata,)).fetchall()
    classifica_giornata = []
    utenti = conn.execute("SELECT id, nome_utente FROM utenti").fetchall()
    for utente in utenti:
        punti_utente = 0
        punti_per_partita = {}
        for partita in partite_reali:
            pronostico = conn.execute("SELECT * FROM pronostici_giornata WHERE id_utente = ? AND id_partita = ?", (utente['id'], partita['id'])).fetchone()
            punti_partita = 0
            punti_dettaglio = {'esito': 0, 'risultato': 0, 'marcatore': 0, 'bonus': 0}
            esito_corretto, risultato_corretto, marcatore_corretto = False, False, False
            if pronostico:
                esito_reale = "1" if partita['risultato_casa_reale'] > partita['risultato_ospite_reale'] else "X" if partita['risultato_casa_reale'] == partita['risultato_ospite_reale'] else "2"
                if pronostico['esito_pronosticato'] == esito_reale:
                    punti_dettaglio['esito'] = 1
                    esito_corretto = True
                if pronostico['risultato_casa_pronosticato'] == partita['risultato_casa_reale'] and pronostico['risultato_ospite_pronosticato'] == partita['risultato_ospite_reale']:
                    punti_dettaglio['risultato'] = 3
                    risultato_corretto = True
                pronostico_marcatore = pronostico['marcatore_pronosticato'].strip().lower() if pronostico['marcatore_pronosticato'] else ""
                marcatori_reali = [m.strip().lower() for m in partita['marcatore_reale'].split(',')] if partita['marcatore_reale'] else []
                if pronostico_marcatore == "nessun marcatore":
                    if partita['risultato_casa_reale'] == 0 and partita['risultato_ospite_reale'] == 0:
                        punti_dettaglio['marcatore'] = 2
                        marcatore_corretto = True
                elif pronostico_marcatore and pronostico_marcatore in marcatori_reali:
                    punti_dettaglio['marcatore'] = 2
                    marcatore_corretto = True
                if esito_corretto and risultato_corretto and marcatore_corretto:
                    punti_dettaglio['bonus'] = 1
            punti_partita = sum(punti_dettaglio.values())
            punti_dettaglio['totale'] = punti_partita
            punti_per_partita[partita['id']] = punti_dettaglio
            punti_utente += punti_partita
        classifica_giornata.append({'nome_utente': utente['nome_utente'],'punti_totali': punti_utente,'punti_per_partita': punti_per_partita})
    conn.close()
    classifica_giornata = sorted(classifica_giornata, key=lambda x: x['punti_totali'], reverse=True)
    return render_template("visualizza_giornata.html", giornata=giornata, partite=partite_reali, classifica=classifica_giornata, session=session)

@app.route("/admin/gestisci-pronostici/<int:giornata>", methods=["GET", "POST"])
def admin_gestisci_pronostici(giornata):
    if 'nome_utente' not in session or session['nome_utente'] != 'mirko': return "Accesso negato.", 403
    conn = get_db_connection()
    view_mode = request.args.get('view', 'utente')
    if request.method == "GET":
        action, pronostico_id = request.args.get('action'), request.args.get('id_pronostico')
        if action == 'cancella' and pronostico_id:
            conn.execute("DELETE FROM pronostici_giornata WHERE id = ?", (pronostico_id,))
            conn.commit()
            conn.close()
            return redirect(url_for('admin_gestisci_pronostici', giornata=giornata, view=view_mode))
    if request.method == "POST":
        action, pronostico_id = request.form.get('action'), request.form.get('id_pronostico')
        if action == 'modifica':
            esito, ris_casa, ris_ospite, marcatore = request.form.get('esito'), request.form.get('risultato_casa'), request.form.get('risultato_ospite'), request.form.get('marcatore')
            conn.execute("UPDATE pronostici_giornata SET esito_pronosticato = ?, risultato_casa_pronosticato = ?, risultato_ospite_pronosticato = ?, marcatore_pronosticato = ? WHERE id = ?", (esito, ris_casa, ris_ospite, marcatore, pronostico_id))
            conn.commit()
            conn.close()
            return redirect(url_for('admin_gestisci_pronostici', giornata=giornata, view=request.args.get('view', 'utente')))
    giornata_stato = conn.execute("SELECT * FROM stato_giornata WHERE giornata = ?", (giornata,)).fetchone()
    if not giornata_stato or giornata_stato['is_in_archivio']:
        return "Questa giornata è già archiviata. Non è possibile modificare i pronostici.", 403
    utenti = conn.execute("SELECT id, nome_utente FROM utenti").fetchall()
    partite = conn.execute("SELECT * FROM partite WHERE giornata = ? AND pronosticabile = 1", (giornata,)).fetchall()
    giocatori_per_partita = {}
    for partita in partite:
        squadra_casa = partita['squadra_casa'].upper()
        squadra_ospite = partita['squadra_ospite'].upper()
        giocatori = conn.execute("SELECT nome_giocatore, squadra FROM giocatori WHERE squadra = ? OR squadra = ? ORDER BY squadra, nome_giocatore", (squadra_casa, squadra_ospite)).fetchall()
        giocatori_per_partita[partita['id']] = giocatori
    pronostici_per_utente = {utente['nome_utente']: {p['id_partita']: p for p in conn.execute("SELECT * FROM pronostici_giornata WHERE id_utente = ? AND id_partita IN (SELECT id FROM partite WHERE giornata = ?)", (utente['id'], giornata)).fetchall()} for utente in utenti}
    pronostici_per_partita = {partita['id']: [] for partita in partite}
    tutti_pronostici = conn.execute("SELECT u.nome_utente, pg.* FROM pronostici_giornata pg JOIN utenti u ON pg.id_utente = u.id WHERE pg.id_partita IN (SELECT id FROM partite WHERE giornata = ? AND pronosticabile = 1)", (giornata,)).fetchall()
    for pronostico in tutti_pronostici:
        partita_id = pronostico['id_partita']
        if partita_id in pronostici_per_partita:
            pronostici_per_partita[partita_id].append(pronostico)
    conn.close()
    return render_template('admin_gestisci_pronostici.html', giornata=giornata, utenti=utenti, partite=partite, pronostici_per_utente=pronostici_per_utente, pronostici_per_partita=pronostici_per_partita, view_mode=view_mode, giocatori_per_partita=giocatori_per_partita, session=session)

@app.route("/admin/gestisci-finalizzazione")
def admin_gestisci_finalizzazione():
    if 'nome_utente' not in session or session['nome_utente'] != 'mirko': return "Accesso negato.", 403
    conn = get_db_connection()
    lock_status = conn.execute("SELECT is_locked FROM stato_pronostici_iniziali WHERE id = 1").fetchone()
    is_locked = lock_status['is_locked'] if lock_status else False
    conn.close()
    return render_template("admin_finalizzazione.html", is_locked=is_locked, session=session)

@app.route("/admin/blocca-pronostici-iniziali", methods=["POST"])
def blocca_pronostici_iniziali():
    if 'nome_utente' not in session or session['nome_utente'] != 'mirko': return "Accesso negato.", 403
    conn = get_db_connection()
    conn.execute("UPDATE stato_pronostici_iniziali SET is_locked = 1 WHERE id = 1")
    conn.commit()
    conn.close()
    return redirect(url_for('admin_gestisci_finalizzazione'))

@app.route("/admin/sblocca-pronostici-iniziali", methods=["POST"])
def sblocca_pronostici_iniziali():
    if 'nome_utente' not in session or session['nome_utente'] != 'mirko': return "Accesso negato.", 403
    conn = get_db_connection()
    conn.execute("UPDATE stato_pronostici_iniziali SET is_locked = 0 WHERE id = 1")
    conn.commit()
    conn.close()
    return redirect(url_for('admin_gestisci_finalizzazione'))

@app.route("/admin/calcola-punti-finali", methods=["GET", "POST"])
def admin_calcola_punti_finali():
    if 'nome_utente' not in session or session['nome_utente'] != 'mirko': return "Accesso negato.", 403
    conn = get_db_connection()
    messaggio = None
    if request.method == 'POST':
        squadra_1, squadra_2, squadra_3, squadra_4, capocannoniere = request.form['squadra_1'], request.form['squadra_2'], request.form['squadra_3'], request.form['squadra_4'], request.form['capocannoniere']
        conn.execute("UPDATE risultati_finali SET squadra_1 = ?, squadra_2 = ?, squadra_3 = ?, squadra_4 = ?, capocannoniere = ? WHERE id = 1", (squadra_1, squadra_2, squadra_3, squadra_4, capocannoniere))
        conn.commit()
        messaggio = ricalcola_punteggi_finali()
    risultati_finali = conn.execute("SELECT * FROM risultati_finali WHERE id = 1").fetchone()
    conn.close()
    return render_template("admin_calcola_punti_finali.html", risultati_finali=risultati_finali, messaggio=messaggio, session=session)

def ricalcola_punteggi_finali():
    conn = get_db_connection()
    risultati_finali = conn.execute("SELECT * FROM risultati_finali WHERE id = 1").fetchone()
    if not (risultati_finali['squadra_1'] and risultati_finali['capocannoniere']):
        conn.close()
        return "Errore: Inserire almeno la prima classificata e il capocannoniere prima di calcolare."
    ricalcola_punteggi_totali()
    utenti = conn.execute("SELECT id FROM utenti").fetchall()
    for utente in utenti:
        punti_finali = 0
        pronostico = conn.execute("SELECT * FROM pronostici_iniziali WHERE id_utente = ?", (utente['id'],)).fetchone()
        if pronostico:
            posizioni_corrette = 0
            if pronostico['squadra_1'].strip().lower() == risultati_finali['squadra_1'].strip().lower(): punti_finali += 20; posizioni_corrette += 1
            if pronostico['squadra_2'].strip().lower() == risultati_finali['squadra_2'].strip().lower(): punti_finali += 20; posizioni_corrette += 1
            if pronostico['squadra_3'].strip().lower() == risultati_finali['squadra_3'].strip().lower(): punti_finali += 20; posizioni_corrette += 1
            if pronostico['squadra_4'].strip().lower() == risultati_finali['squadra_4'].strip().lower(): punti_finali += 20; posizioni_corrette += 1
            if posizioni_corrette == 4: punti_finali += 10
            if pronostico['capocannoniere'].strip().lower() == risultati_finali['capocannoniere'].strip().lower(): punti_finali += 20
            conn.execute("UPDATE punteggi SET punteggio_totale = punteggio_totale + ? WHERE id_utente = ?", (punti_finali, utente['id']))
    conn.commit()
    conn.close()
    return "Punti finali calcolati e aggiunti alla classifica generale con successo!"

# --- BLOCCO RICALCOLO CORRETTO ---
PUNTEGGI_BASE_GIORNATE_1_E_2 = {
    "LV88": 15,
    "Porcodio88": 15,
    "nicola.NIB93": 15,
    "Marco Girometti": 14,
    "Pinne85": 14,
    "Roberto Zucchinali": 14,
    "Campione in carica": 12,
    "Mapelli’s": 10,
    "Niko91": 10,
    "A.s Tronzo": 9,
    "Fedesalvi6497": 9,
    "Arzu88": 8,
    "alefeninno": 8,
    "BirraReal": 7,
    "Garghe.": 7,
    "Mirko": 7,
    "J-kdb": 5,
    "Conso": 4,
    "mirko": 4,
    "NB93": 3,
}

def inserisci_punteggi_base(conn):
    cursor = conn.cursor()
    cursor.execute("DELETE FROM punteggi")
    print("Tabella punteggi svuotata.")
    utenti_non_trovati = []

    for nome_utente, punteggio in PUNTEGGI_BASE_GIORNATE_1_E_2.items():
        # LA MAGIA È QUI: LOWER() e TRIM() ignorano maiuscole/minuscole e spazi vuoti!
        utente_row = cursor.execute(
            "SELECT id FROM utenti WHERE LOWER(TRIM(nome_utente)) = LOWER(TRIM(?))",
            (nome_utente,)
        ).fetchone()

        if utente_row:
            id_utente = utente_row['id']
            cursor.execute("INSERT OR IGNORE INTO punteggi (id_utente, punteggio_totale) VALUES (?, ?)", (id_utente, 0))
            cursor.execute("UPDATE punteggi SET punteggio_totale = ? WHERE id_utente = ?", (punteggio, id_utente))
        else:
            utenti_non_trovati.append(nome_utente)

    cursor.execute("INSERT OR IGNORE INTO punteggi (id_utente, punteggio_totale) SELECT id, 0 FROM utenti WHERE id NOT IN (SELECT id_utente FROM punteggi)")

    if utenti_non_trovati:
        return f"ATTENZIONE: Utenti non trovati: {', '.join(utenti_non_trovati)}"
    return "Punteggi base delle giornate 1 e 2 inseriti correttamente."

@app.route("/admin/ricalcola-tutto")
def admin_ricalcola_tutta_la_classifica():
    if 'nome_utente' not in session or session['nome_utente'] != 'mirko':
        return "Accesso negato.", 403
    conn = get_db_connection()
    messaggio_base = inserisci_punteggi_base(conn)
    giornate_da_ricalcolare = conn.execute("SELECT giornata FROM stato_giornata WHERE is_in_archivio = 1 AND giornata > 2 ORDER BY giornata").fetchall()
    messaggi_calcolo = []
    for giornata_row in giornate_da_ricalcolare:
        giornata_num = giornata_row['giornata']
        # --- Modifica per efficienza: passiamo la connessione esistente ---
        _calcola_e_aggiorna_punti_giornata_con_conn(giornata_num, conn)
        messaggi_calcolo.append(f"Giornata {giornata_num} ricalcolata.")
    conn.commit()
    conn.close()
    messaggio_finale = f"Ricalcolo completato! {messaggio_base} {' '.join(messaggi_calcolo)}"
    session['flash_message'] = messaggio_finale
    return redirect(url_for("admin_home"))

def _calcola_e_aggiorna_punti_giornata_con_conn(giornata, conn):
    """Versione interna di calcola_punti che usa una connessione esistente."""
    utenti = conn.execute("SELECT id FROM utenti").fetchall()
    partite_calcolo = conn.execute("SELECT * FROM partite WHERE giornata = ? AND pronosticabile = 1 AND risultato_casa_reale IS NOT NULL", (giornata,)).fetchall()
    if not partite_calcolo:
        return
    for utente in utenti:
        punti_giornata = 0
        for partita in partite_calcolo:
            pronostico = conn.execute("SELECT * FROM pronostici_giornata WHERE id_utente = ? AND id_partita = ?", (utente['id'], partita['id'])).fetchone()
            if pronostico:
                punti_partita = 0; esito_corretto = False; risultato_corretto = False; marcatore_corretto = False
                esito_reale = "1" if partita['risultato_casa_reale'] > partita['risultato_ospite_reale'] else "X" if partita['risultato_casa_reale'] == partita['risultato_ospite_reale'] else "2"
                if pronostico['esito_pronosticato'] == esito_reale: punti_partita += 1; esito_corretto = True
                if pronostico['risultato_casa_pronosticato'] == partita['risultato_casa_reale'] and pronostico['risultato_ospite_pronosticato'] == partita['risultato_ospite_reale']: punti_partita += 3; risultato_corretto = True
                pronostico_marcatore = pronostico['marcatore_pronosticato'].strip().lower() if pronostico['marcatore_pronosticato'] else ""
                marcatori_reali = [m.strip().lower() for m in partita['marcatore_reale'].split(',')] if partita['marcatore_reale'] else []
                if pronostico_marcatore == "nessun marcatore":
                    if partita['risultato_casa_reale'] == 0 and partita['risultato_ospite_reale'] == 0:
                        punti_partita += 2; marcatore_corretto = True
                elif pronostico_marcatore and pronostico_marcatore in marcatori_reali:
                    punti_partita += 2; marcatore_corretto = True
                if esito_corretto and risultato_corretto and marcatore_corretto: punti_partita += 1
                punti_giornata += punti_partita
        conn.execute("INSERT INTO punteggi (id_utente, punteggio_totale) VALUES (?, 0) ON CONFLICT(id_utente) DO NOTHING", (utente['id'],))
        conn.execute("UPDATE punteggi SET punteggio_totale = punteggio_totale + ? WHERE id_utente = ?", (punti_giornata, utente['id']))

if __name__ == "__main__":
    create_tables()
    app.run(debug=True)