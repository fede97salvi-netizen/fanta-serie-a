import sys
import os
from flask import Flask, render_template, request, redirect, url_for, session, flash
import hashlib
import smtplib
import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import random
import string
from datetime import datetime, timedelta
import pytz

# --- DATABASE: SQLite in locale, PostgreSQL su Render ---
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
if DATABASE_URL:
    import psycopg2
    import psycopg2.extras
    USE_POSTGRES = True
else:
    import sqlite3
    USE_POSTGRES = False

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'chiave_segreta_molto_segreta')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)


# ─────────────────────────────────────────────
# CONNESSIONE DATABASE (SQLite o PostgreSQL)
# ─────────────────────────────────────────────

def get_db_connection():
    if USE_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        return conn
    else:
        conn = sqlite3.connect(os.path.join(app.root_path, 'database.db'), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

def db_execute(conn, query, params=()):
    """Esegue una query adattando i placeholder (? per SQLite, %s per PostgreSQL)."""
    if USE_POSTGRES:
        query = query.replace('?', '%s')
        query = query.replace('INTEGER PRIMARY KEY AUTOINCREMENT', 'SERIAL PRIMARY KEY')
        query = query.replace('ON CONFLICT(id_utente) DO NOTHING', 'ON CONFLICT (id_utente) DO NOTHING')
    cur = conn.cursor()
    cur.execute(query, params)
    return cur

def db_fetchone(conn, query, params=()):
    cur = db_execute(conn, query, params)
    return cur.fetchone()

def db_fetchall(conn, query, params=()):
    cur = db_execute(conn, query, params)
    return cur.fetchall()

def db_commit(conn):
    conn.commit()

def row_get(row, key):
    """Accede a una riga sia da sqlite3.Row che da psycopg2 RealDictRow."""
    if row is None:
        return None
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


# ─────────────────────────────────────────────
# CONTEXT PROCESSOR — giornata attiva globale
# ─────────────────────────────────────────────

@app.context_processor
def inject_giornata_attiva():
    try:
        conn = get_db_connection()
        row = db_fetchone(conn, "SELECT giornata FROM stato_giornata WHERE is_attiva = TRUE")
        conn.close()
        return {'giornata_attiva': row_get(row, 'giornata') if row else None}
    except Exception:
        return {'giornata_attiva': None}


# ─────────────────────────────────────────────
# UTILITY
# ─────────────────────────────────────────────

def parse_flexible_datetime(date_string):
    if not date_string:
        return None
    try:
        return datetime.strptime(date_string, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        try:
            return datetime.strptime(date_string, "%Y-%m-%dT%H:%M")
        except ValueError:
            return None

@app.template_filter('fuso_orario_italia')
def fuso_orario_italia(data_ora_utc_str):
    if not data_ora_utc_str:
        return 'Non impostato'
    try:
        roma_tz = pytz.timezone('Europe/Rome')
        orario_naive = parse_flexible_datetime(str(data_ora_utc_str))
        if orario_naive is None:
            return str(data_ora_utc_str)
        orario_utc = pytz.utc.localize(orario_naive)
        return orario_utc.astimezone(roma_tz).strftime("%d/%m/%Y alle %H:%M")
    except (ValueError, TypeError):
        return str(data_ora_utc_str)


# ─────────────────────────────────────────────
# CREAZIONE TABELLE
# ─────────────────────────────────────────────

def create_tables():
    conn = get_db_connection()
    if USE_POSTGRES:
        db_execute(conn, """CREATE TABLE IF NOT EXISTS utenti (
            id SERIAL PRIMARY KEY,
            nome_utente TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            is_temp_password BOOLEAN NOT NULL DEFAULT FALSE,
            email TEXT)""")
        # Migrazione: aggiungi email se non esiste
        try:
            db_execute(conn, "ALTER TABLE utenti ADD COLUMN IF NOT EXISTS email TEXT")
        except Exception:
            pass
        db_execute(conn, """CREATE TABLE IF NOT EXISTS pronostici_iniziali (
            id SERIAL PRIMARY KEY,
            id_utente INTEGER NOT NULL REFERENCES utenti(id),
            squadra_1 TEXT, squadra_2 TEXT, squadra_3 TEXT, squadra_4 TEXT, capocannoniere TEXT)""")
        db_execute(conn, """CREATE TABLE IF NOT EXISTS partite (
            id SERIAL PRIMARY KEY,
            giornata INTEGER NOT NULL,
            squadra_casa TEXT NOT NULL, squadra_ospite TEXT NOT NULL,
            risultato_casa_reale INTEGER, risultato_ospite_reale INTEGER,
            marcatore_reale TEXT,
            pronosticabile BOOLEAN NOT NULL DEFAULT FALSE,
            data_ora_partita TEXT)""")
        db_execute(conn, """CREATE TABLE IF NOT EXISTS pronostici_giornata (
            id SERIAL PRIMARY KEY,
            id_utente INTEGER NOT NULL REFERENCES utenti(id),
            id_partita INTEGER NOT NULL REFERENCES partite(id),
            esito_pronosticato TEXT,
            risultato_casa_pronosticato INTEGER,
            risultato_ospite_pronosticato INTEGER,
            marcatore_pronosticato TEXT)""")
        db_execute(conn, """CREATE TABLE IF NOT EXISTS punteggi (
            id SERIAL PRIMARY KEY,
            id_utente INTEGER NOT NULL UNIQUE REFERENCES utenti(id),
            punteggio_totale INTEGER NOT NULL DEFAULT 0)""")
        db_execute(conn, """CREATE TABLE IF NOT EXISTS stato_giornata (
            id SERIAL PRIMARY KEY,
            giornata INTEGER NOT NULL UNIQUE,
            is_attiva BOOLEAN NOT NULL DEFAULT FALSE,
            is_in_archivio BOOLEAN NOT NULL DEFAULT FALSE)""")
        db_execute(conn, """CREATE TABLE IF NOT EXISTS stato_pronostici_iniziali (
            id INTEGER PRIMARY KEY,
            is_locked BOOLEAN NOT NULL DEFAULT FALSE)""")
        db_execute(conn, """CREATE TABLE IF NOT EXISTS risultati_finali (
            id INTEGER PRIMARY KEY,
            squadra_1 TEXT, squadra_2 TEXT, squadra_3 TEXT, squadra_4 TEXT, capocannoniere TEXT)""")
        db_execute(conn, """CREATE TABLE IF NOT EXISTS giocatori (
            id SERIAL PRIMARY KEY,
            nome_giocatore TEXT NOT NULL,
            squadra TEXT NOT NULL)""")
        db_execute(conn, "INSERT INTO stato_pronostici_iniziali (id, is_locked) VALUES (1, FALSE) ON CONFLICT (id) DO NOTHING")
        db_execute(conn, "INSERT INTO risultati_finali (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
    else:
        conn.execute("CREATE TABLE IF NOT EXISTS utenti (id INTEGER PRIMARY KEY AUTOINCREMENT, nome_utente TEXT NOT NULL UNIQUE, password TEXT NOT NULL, is_temp_password BOOLEAN NOT NULL DEFAULT 0, email TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS pronostici_iniziali (id INTEGER PRIMARY KEY AUTOINCREMENT, id_utente INTEGER NOT NULL, squadra_1 TEXT, squadra_2 TEXT, squadra_3 TEXT, squadra_4 TEXT, capocannoniere TEXT, FOREIGN KEY(id_utente) REFERENCES utenti(id))")
        conn.execute("CREATE TABLE IF NOT EXISTS pronostici_giornata (id INTEGER PRIMARY KEY AUTOINCREMENT, id_utente INTEGER NOT NULL, id_partita INTEGER NOT NULL, esito_pronosticato TEXT, risultato_casa_pronosticato INTEGER, risultato_ospite_pronosticato INTEGER, marcatore_pronosticato TEXT, FOREIGN KEY(id_utente) REFERENCES utenti(id), FOREIGN KEY(id_partita) REFERENCES partite(id))")
        conn.execute("CREATE TABLE IF NOT EXISTS partite (id INTEGER PRIMARY KEY AUTOINCREMENT, giornata INTEGER NOT NULL, squadra_casa TEXT NOT NULL, squadra_ospite TEXT NOT NULL, risultato_casa_reale INTEGER, risultato_ospite_reale INTEGER, marcatore_reale TEXT, pronosticabile BOOLEAN NOT NULL DEFAULT 0, data_ora_partita TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS punteggi (id INTEGER PRIMARY KEY AUTOINCREMENT, id_utente INTEGER NOT NULL UNIQUE, punteggio_totale INTEGER NOT NULL DEFAULT 0, FOREIGN KEY(id_utente) REFERENCES utenti(id))")
        conn.execute("CREATE TABLE IF NOT EXISTS stato_giornata (id INTEGER PRIMARY KEY AUTOINCREMENT, giornata INTEGER NOT NULL UNIQUE, is_attiva BOOLEAN NOT NULL DEFAULT 0, is_in_archivio BOOLEAN NOT NULL DEFAULT 0)")
        conn.execute("CREATE TABLE IF NOT EXISTS stato_pronostici_iniziali (id INTEGER PRIMARY KEY, is_locked BOOLEAN NOT NULL DEFAULT 0)")
        conn.execute("INSERT OR IGNORE INTO stato_pronostici_iniziali (id, is_locked) VALUES (1, 0)")
        conn.execute("CREATE TABLE IF NOT EXISTS risultati_finali (id INTEGER PRIMARY KEY, squadra_1 TEXT, squadra_2 TEXT, squadra_3 TEXT, squadra_4 TEXT, capocannoniere TEXT)")
        conn.execute("INSERT OR IGNORE INTO risultati_finali (id) VALUES (1)")
        conn.execute("CREATE TABLE IF NOT EXISTS giocatori (id INTEGER PRIMARY KEY AUTOINCREMENT, nome_giocatore TEXT NOT NULL, squadra TEXT NOT NULL)")
    db_commit(conn)
    conn.close()



# ─────────────────────────────────────────────
# EMAIL
# ─────────────────────────────────────────────

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

def invia_email_async(destinatari, oggetto, corpo_html):
    """Invia email in background senza bloccare la richiesta HTTP."""
    def _invia():
        try:
            print(f"[EMAIL] Avvio invio a {len(destinatari)} destinatari...", flush=True)
            successi, errori = invia_email(destinatari, oggetto, corpo_html)
            print(f"[EMAIL] Completato: {successi} successi, {len(errori)} errori", flush=True)
            if errori:
                for e in errori:
                    print(f"[EMAIL] Errore: {e}", flush=True)
        except Exception as e:
            print(f"[EMAIL] Eccezione nel thread: {e}", flush=True)
    t = threading.Thread(target=_invia)
    t.daemon = True
    t.start()

def invia_email(destinatari, oggetto, corpo_html):
    """Invia email tramite Resend API (HTTP). Restituisce (successi, errori)."""
    if not RESEND_API_KEY:
        return 0, ["Email non configurata (RESEND_API_KEY mancante)"]
    successi = 0
    errori = []
    for dest in destinatari:
        try:
            payload = {
                'from': f'{EMAIL_FROM_NAME} <{EMAIL_FROM_ADDRESS}>',
                'to': [dest],
                'subject': oggetto,
                'html': corpo_html
            }
            r = http_requests.post(
                'https://api.resend.com/emails',
                headers={
                    'Authorization': f'Bearer {RESEND_API_KEY}',
                    'Content-Type': 'application/json'
                },
                json=payload,
                timeout=15
            )
            if r.status_code in (200, 201):
                successi += 1
                print(f"[EMAIL] Inviata a {dest}", flush=True)
            else:
                errore = r.json().get('message', r.text[:100])
                errori.append(f"{dest}: {errore}")
                print(f"[EMAIL] Errore per {dest}: {errore}", flush=True)
        except Exception as e:
            errori.append(f"{dest}: {str(e)}")
            print(f"[EMAIL] Eccezione per {dest}: {e}", flush=True)
    return successi, errori

def converti_data_email(data_ora_utc_str):
    """Converte data UTC in formato leggibile per l'email (fuso orario Italia)."""
    if not data_ora_utc_str:
        return 'Data da definire'
    try:
        roma_tz = pytz.timezone('Europe/Rome')
        orario_naive = parse_flexible_datetime(str(data_ora_utc_str))
        if orario_naive is None:
            return str(data_ora_utc_str)
        orario_utc = pytz.utc.localize(orario_naive)
        return orario_utc.astimezone(roma_tz).strftime("%d/%m/%Y alle %H:%M")
    except Exception:
        return str(data_ora_utc_str)

def build_email_giornata(giornata, partite):
    """Costruisce il corpo HTML dell email di notifica giornata."""
    partite_html = ""
    for p in partite:
        data_str = converti_data_email(p.get('data_ora_partita') or '')
        partite_html += f"""
        <tr>
          <td style="padding:12px 16px;border-bottom:1px solid #e5e7eb;">
            <strong style="font-size:16px;color:#1e3a5f;">{p['squadra_casa']} vs {p['squadra_ospite']}</strong>
            <div style="font-size:13px;color:#6b7280;margin-top:4px;">📅 {data_str}</div>
          </td>
        </tr>"""
    return f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,sans-serif;">
      <table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:32px 16px;">
        <tr><td align="center">
          <table width="100%" style="max-width:520px;background:white;border-radius:16px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08);">
            <tr>
              <td style="background:linear-gradient(135deg,#003f8a,#0f4a1e);padding:28px 24px;text-align:center;">
                <div style="font-size:32px;margin-bottom:8px;">🏆</div>
                <h1 style="color:white;margin:0;font-size:24px;letter-spacing:1px;">FantaSerieA</h1>
                <p style="color:rgba(255,255,255,0.8);margin:8px 0 0;font-size:14px;">Giornata {giornata} — Inserisci i tuoi pronostici!</p>
              </td>
            </tr>
            <tr>
              <td style="padding:24px;">
                <p style="color:#374151;font-size:15px;margin:0 0 16px;">Le partite della <strong>giornata {giornata}</strong> sono pronte. Hai tempo fino all'inizio di ogni match per inserire i tuoi pronostici.</p>
                <h2 style="color:#1e3a5f;font-size:16px;margin:0 0 12px;text-transform:uppercase;letter-spacing:1px;">Le 3 partite da pronosticare</h2>
                <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e5e7eb;border-radius:10px;overflow:hidden;">
                  {partite_html}
                </table>
                <div style="text-align:center;margin-top:24px;">
                  <a href="https://fanta-serie-a-1.onrender.com/pronostici-giornata/{giornata}"
                     style="background:linear-gradient(135deg,#1565c0,#0090d4);color:white;padding:12px 32px;border-radius:8px;text-decoration:none;font-weight:bold;font-size:15px;display:inline-block;">
                    Inserisci i pronostici →
                  </a>
                </div>
                <p style="color:#9ca3af;font-size:12px;text-align:center;margin-top:24px;">
                  Ricevi questa email perché sei iscritto a FantaSerieA.<br>
                  <a href="https://fanta-serie-a-1.onrender.com" style="color:#0090d4;">Vai all'app</a>
                </p>
              </td>
            </tr>
          </table>
        </td></tr>
      </table>
    </body>
    </html>"""

# ─────────────────────────────────────────────
# ROUTE PUBBLICHE
# ─────────────────────────────────────────────

@app.route("/")
def home():
    if 'nome_utente' not in session:
        return render_template("welcome.html", session=session)
    conn = get_db_connection()
    giornata_row = db_fetchone(conn, "SELECT giornata FROM stato_giornata WHERE is_attiva = TRUE")
    giornata_attiva = row_get(giornata_row, 'giornata') if giornata_row else None
    user = db_fetchone(conn, "SELECT id FROM utenti WHERE nome_utente = ?", (session['nome_utente'],))
    if not user:
        return redirect(url_for('logout'))
    user_id = row_get(user, 'id')
    punteggio_row = db_fetchone(conn, "SELECT punteggio_totale FROM punteggi WHERE id_utente = ?", (user_id,))
    punteggio_utente = row_get(punteggio_row, 'punteggio_totale') or 0
    posizione_row = db_fetchone(conn, "SELECT COUNT(id) + 1 as rank FROM punteggi WHERE punteggio_totale > ?", (punteggio_utente,))
    posizione_utente = row_get(posizione_row, 'rank') or 1
    conn.close()
    return render_template("home.html", giornata_attiva=giornata_attiva, punteggio_utente=punteggio_utente, posizione_utente=posizione_utente, session=session)

@app.route("/registrazione", methods=["GET", "POST"])
def registrazione():
    if request.method == "POST":
        nome_utente = request.form["nome_utente"]
        password_hash = hashlib.sha256(request.form["password"].encode()).hexdigest()
        try:
            conn = get_db_connection()
            db_execute(conn, "INSERT INTO utenti (nome_utente, password) VALUES (?, ?)", (nome_utente, password_hash))
            db_commit(conn)
            conn.close()
            session['nome_utente'] = nome_utente
            return redirect(url_for("home"))
        except Exception:
            return render_template("registrazione.html", session=session, errore="Nome utente già esistente. Scegli un altro nome.")
    return render_template("registrazione.html", session=session)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        nome_utente = request.form["nome_utente"]
        password_hash = hashlib.sha256(request.form["password"].encode()).hexdigest()
        conn = get_db_connection()
        user = db_fetchone(conn, "SELECT * FROM utenti WHERE nome_utente = ? AND password = ?", (nome_utente, password_hash))
        conn.close()
        if user:
            if request.form.get('remember'):
                session.permanent = True
            session['nome_utente'] = nome_utente
            if row_get(user, 'is_temp_password'):
                return redirect(url_for('cambia_password'))
            return redirect(url_for("home"))
        return render_template("login.html", session=session, errore="Credenziali non valide. Riprova.")
    return render_template("login.html", session=session)

@app.route("/cambia-password", methods=["GET", "POST"])
def cambia_password():
    if 'nome_utente' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        nuova_password = request.form['nuova_password']
        if nuova_password != request.form['conferma_password']:
            return render_template('cambia_password.html', session=session, errore="Le password non coincidono.")
        password_hash = hashlib.sha256(nuova_password.encode()).hexdigest()
        conn = get_db_connection()
        db_execute(conn, "UPDATE utenti SET password = ?, is_temp_password = FALSE WHERE nome_utente = ?", (password_hash, session['nome_utente']))
        db_commit(conn)
        conn.close()
        return redirect(url_for('home'))
    return render_template('cambia_password.html', session=session)

@app.route("/logout")
def logout():
    session.pop('nome_utente', None)
    return redirect(url_for("home"))

@app.route("/classifica")
def classifica():
    if 'nome_utente' not in session:
        return redirect(url_for("login"))
    conn = get_db_connection()
    classifica_utenti = db_fetchall(conn, "SELECT u.nome_utente, p.punteggio_totale FROM utenti u JOIN punteggi p ON u.id = p.id_utente ORDER BY p.punteggio_totale DESC")
    conn.close()
    return render_template("classifica.html", classifica=classifica_utenti, session=session)


@app.route("/giornate")
def archivio_giornate():
    if 'nome_utente' not in session:
        return redirect(url_for("login"))
    conn = get_db_connection()
    giornate = db_fetchall(conn, "SELECT * FROM stato_giornata WHERE is_in_archivio = TRUE ORDER BY giornata")
    conn.close()
    return render_template("archivio_giornate.html", giornate=giornate, session=session)

@app.route("/giornata/<int:giornata>")
def visualizza_giornata(giornata):
    if 'nome_utente' not in session:
        return redirect(url_for("login"))
    conn = get_db_connection()
    partite_reali = db_fetchall(conn, "SELECT * FROM partite WHERE giornata = ? AND risultato_casa_reale IS NOT NULL", (giornata,))
    utenti = db_fetchall(conn, "SELECT id, nome_utente FROM utenti")
    classifica_giornata = []
    for utente in utenti:
        uid = row_get(utente, 'id')
        punti_utente = 0
        punti_per_partita = {}
        for partita in partite_reali:
            pid = row_get(partita, 'id')
            pronostico = db_fetchone(conn, "SELECT * FROM pronostici_giornata WHERE id_utente = ? AND id_partita = ?", (uid, pid))
            punti_dettaglio = {'esito': 0, 'risultato': 0, 'marcatore': 0, 'bonus': 0}
            esito_corretto = risultato_corretto = marcatore_corretto = False
            if pronostico:
                r_casa = row_get(partita, 'risultato_casa_reale')
                r_osp = row_get(partita, 'risultato_ospite_reale')
                esito_reale = "1" if r_casa > r_osp else "X" if r_casa == r_osp else "2"
                if row_get(pronostico, 'esito_pronosticato') == esito_reale:
                    punti_dettaglio['esito'] = 1; esito_corretto = True
                if row_get(pronostico, 'risultato_casa_pronosticato') == r_casa and row_get(pronostico, 'risultato_ospite_pronosticato') == r_osp:
                    punti_dettaglio['risultato'] = 3; risultato_corretto = True
                pm = (row_get(pronostico, 'marcatore_pronosticato') or '').strip().lower()
                mr_raw = row_get(partita, 'marcatore_reale') or ''
                marcatori_reali = [m.strip().lower() for m in mr_raw.split(',') if m.strip()]
                if pm == "nessun marcatore":
                    if r_casa == 0 and r_osp == 0:
                        punti_dettaglio['marcatore'] = 2; marcatore_corretto = True
                elif pm and pm in marcatori_reali:
                    punti_dettaglio['marcatore'] = 2; marcatore_corretto = True
                if esito_corretto and risultato_corretto and marcatore_corretto:
                    punti_dettaglio['bonus'] = 1
            punti_dettaglio['totale'] = sum(punti_dettaglio.values())
            punti_per_partita[pid] = punti_dettaglio
            punti_utente += punti_dettaglio['totale']
        classifica_giornata.append({'nome_utente': row_get(utente, 'nome_utente'), 'punti_totali': punti_utente, 'punti_per_partita': punti_per_partita})
    classifica_giornata.sort(key=lambda x: x['punti_totali'], reverse=True)
    # Costruisci dizionario pronostici per partita e utente per la sezione pronostici
    pronostici_per_partita = {}
    for partita in partite_reali:
        pid = row_get(partita, 'id')
        rows = db_fetchall(conn, "SELECT u.nome_utente, pg.esito_pronosticato, pg.risultato_casa_pronosticato, pg.risultato_ospite_pronosticato, pg.marcatore_pronosticato FROM pronostici_giornata pg JOIN utenti u ON pg.id_utente = u.id WHERE pg.id_partita = ?", (pid,))
        pronostici_per_partita[pid] = {
            row_get(r, 'nome_utente'): {
                'esito': row_get(r, 'esito_pronosticato'),
                'r_casa': row_get(r, 'risultato_casa_pronosticato'),
                'r_osp': row_get(r, 'risultato_ospite_pronosticato'),
                'marcatore': row_get(r, 'marcatore_pronosticato'),
            } for r in rows
        }
    conn.close()
    return render_template("visualizza_giornata.html", giornata=giornata, partite=partite_reali, classifica=classifica_giornata, pronostici_per_partita=pronostici_per_partita, session=session)

@app.route("/pronostici-iniziali", methods=["GET", "POST"])
def pronostici_iniziali():
    if 'nome_utente' not in session:
        return redirect(url_for("login"))
    conn = get_db_connection()
    try:
        lock_row = db_fetchone(conn, "SELECT is_locked FROM stato_pronostici_iniziali WHERE id = 1")
        is_locked = row_get(lock_row, 'is_locked') if lock_row else True
        user = db_fetchone(conn, "SELECT id FROM utenti WHERE nome_utente = ?", (session['nome_utente'],))
        if not user:
            return redirect(url_for('logout'))
        user_id = row_get(user, 'id')
        if is_locked:
            pronostici_tutti = db_fetchall(conn, "SELECT u.nome_utente, pi.* FROM pronostici_iniziali pi JOIN utenti u ON pi.id_utente = u.id ORDER BY u.nome_utente")
            return render_template("pronostici_iniziali.html", is_locked=is_locked, pronostici_tutti=pronostici_tutti, session=session)
        if request.method == "POST":
            s1, s2, s3, s4, cc = request.form["squadra_1"], request.form["squadra_2"], request.form["squadra_3"], request.form["squadra_4"], request.form["capocannoniere"]
            esiste = db_fetchone(conn, "SELECT id FROM pronostici_iniziali WHERE id_utente = ?", (user_id,))
            if esiste:
                db_execute(conn, "UPDATE pronostici_iniziali SET squadra_1=?, squadra_2=?, squadra_3=?, squadra_4=?, capocannoniere=? WHERE id_utente=?", (s1, s2, s3, s4, cc, user_id))
            else:
                db_execute(conn, "INSERT INTO pronostici_iniziali (id_utente, squadra_1, squadra_2, squadra_3, squadra_4, capocannoniere) VALUES (?,?,?,?,?,?)", (user_id, s1, s2, s3, s4, cc))
            db_commit(conn)
            return redirect(url_for("home"))
        pronostico = db_fetchone(conn, "SELECT * FROM pronostici_iniziali WHERE id_utente = ?", (user_id,))
        return render_template("pronostici_iniziali.html", is_locked=is_locked, pronostico=pronostico, session=session)
    finally:
        conn.close()

@app.route("/pronostici-giornata/<int:giornata>", methods=["GET", "POST"])
def pronostici_giornata(giornata):
    if 'nome_utente' not in session:
        return redirect(url_for("login"))
    conn = get_db_connection()
    user = db_fetchone(conn, "SELECT id FROM utenti WHERE nome_utente = ?", (session['nome_utente'],))
    user_id = row_get(user, 'id')
    partite = db_fetchall(conn, "SELECT * FROM partite WHERE giornata = ? AND pronosticabile = TRUE", (giornata,))
    giocatori_per_partita = {}
    for partita in partite:
        sc = row_get(partita, 'squadra_casa').upper()
        so = row_get(partita, 'squadra_ospite').upper()
        giocatori_per_partita[row_get(partita, 'id')] = db_fetchall(conn, "SELECT nome_giocatore, squadra FROM giocatori WHERE squadra = ? OR squadra = ? ORDER BY squadra, nome_giocatore", (sc, so))
    roma_tz = pytz.timezone('Europe/Rome')
    ora_corrente = datetime.now(roma_tz)
    pronostici_salvati = db_fetchall(conn, "SELECT * FROM pronostici_giornata WHERE id_utente = ? AND id_partita IN (SELECT id FROM partite WHERE giornata = ?)", (user_id, giornata))
    pronostici_dict = {row_get(p, 'id_partita'): p for p in pronostici_salvati}
    scadenze_dict = {}
    pronostici_altri_utenti = {}

    def is_partita_scaduta(partita):
        dop = row_get(partita, 'data_ora_partita')
        if not dop:
            return False
        orario_naive = parse_flexible_datetime(str(dop))
        if not orario_naive:
            return False
        return ora_corrente > pytz.utc.localize(orario_naive).astimezone(roma_tz)

    if request.method == "POST":
        for partita in partite:
            if not is_partita_scaduta(partita):
                pid = row_get(partita, 'id')
                esito = request.form.get(f"esito_{pid}")
                r_casa = request.form.get(f"risultato_casa_{pid}")
                r_osp = request.form.get(f"risultato_ospite_{pid}")
                marcatore = request.form.get(f"marcatore_{pid}")
                if esito or (r_casa and r_osp) or marcatore:
                    if pid in pronostici_dict:
                        db_execute(conn, "UPDATE pronostici_giornata SET esito_pronosticato=?, risultato_casa_pronosticato=?, risultato_ospite_pronosticato=?, marcatore_pronosticato=? WHERE id_utente=? AND id_partita=?", (esito, r_casa, r_osp, marcatore, user_id, pid))
                    else:
                        db_execute(conn, "INSERT INTO pronostici_giornata (id_utente, id_partita, esito_pronosticato, risultato_casa_pronosticato, risultato_ospite_pronosticato, marcatore_pronosticato) VALUES (?,?,?,?,?,?)", (user_id, pid, esito, r_casa, r_osp, marcatore))
        db_commit(conn)
        conn.close()
        return redirect(url_for("home"))

    for partita in partite:
        pid = row_get(partita, 'id')
        scaduto = is_partita_scaduta(partita)
        scadenze_dict[pid] = scaduto
        if scaduto:
            pronostici_altri_utenti[pid] = db_fetchall(conn, "SELECT u.nome_utente, pg.* FROM pronostici_giornata pg JOIN utenti u ON pg.id_utente = u.id WHERE pg.id_partita = ?", (pid,))
    conn.close()
    return render_template("pronostici_giornata.html", partite=partite, giornata=giornata, pronostici_per_partita=pronostici_dict, scadenze=scadenze_dict, pronostici_altri_utenti=pronostici_altri_utenti, giocatori_per_partita=giocatori_per_partita, session=session)


# ─────────────────────────────────────────────
# LOGICA PUNTEGGI
# ─────────────────────────────────────────────

def _calcola_punti_giornata_conn(giornata, conn):
    utenti = db_fetchall(conn, "SELECT id FROM utenti")
    partite = db_fetchall(conn, "SELECT * FROM partite WHERE giornata = ? AND pronosticabile = TRUE AND risultato_casa_reale IS NOT NULL", (giornata,))
    if not partite:
        return
    for utente in utenti:
        uid = row_get(utente, 'id')
        punti_giornata = 0
        for partita in partite:
            pid = row_get(partita, 'id')
            pronostico = db_fetchone(conn, "SELECT * FROM pronostici_giornata WHERE id_utente = ? AND id_partita = ?", (uid, pid))
            if pronostico:
                punti = 0
                r_casa = row_get(partita, 'risultato_casa_reale')
                r_osp = row_get(partita, 'risultato_ospite_reale')
                esito_reale = "1" if r_casa > r_osp else "X" if r_casa == r_osp else "2"
                ec = row_get(pronostico, 'esito_pronosticato') == esito_reale
                rc = row_get(pronostico, 'risultato_casa_pronosticato') == r_casa and row_get(pronostico, 'risultato_ospite_pronosticato') == r_osp
                pm = (row_get(pronostico, 'marcatore_pronosticato') or '').strip().lower()
                mr_raw = row_get(partita, 'marcatore_reale') or ''
                marcatori_reali = [m.strip().lower() for m in mr_raw.split(',') if m.strip()]
                mc = False
                if pm == "nessun marcatore":
                    mc = (r_casa == 0 and r_osp == 0)
                elif pm:
                    mc = pm in marcatori_reali
                if ec: punti += 1
                if rc: punti += 3
                if mc: punti += 2
                if ec and rc and mc: punti += 1
                punti_giornata += punti
        if USE_POSTGRES:
            db_execute(conn, "INSERT INTO punteggi (id_utente, punteggio_totale) VALUES (?, 0) ON CONFLICT (id_utente) DO NOTHING", (uid,))
        else:
            db_execute(conn, "INSERT INTO punteggi (id_utente, punteggio_totale) VALUES (?, 0) ON CONFLICT(id_utente) DO NOTHING", (uid,))
        db_execute(conn, "UPDATE punteggi SET punteggio_totale = punteggio_totale + ? WHERE id_utente = ?", (punti_giornata, uid))

def calcola_e_aggiorna_punti_giornata(giornata):
    conn = get_db_connection()
    partite_check = db_fetchall(conn, "SELECT id FROM partite WHERE giornata = ? AND pronosticabile = TRUE AND risultato_casa_reale IS NOT NULL", (giornata,))
    if not partite_check:
        conn.close()
        return f"Nessuna partita con risultati trovata per la giornata {giornata}."
    _calcola_punti_giornata_conn(giornata, conn)
    db_commit(conn)
    conn.close()
    return f"Punti per la Giornata {giornata} calcolati con successo!"

def ricalcola_punteggi_totali():
    conn = get_db_connection()
    db_execute(conn, "DELETE FROM punteggi")
    utenti = db_fetchall(conn, "SELECT id FROM utenti")
    for utente in utenti:
        db_execute(conn, "INSERT INTO punteggi (id_utente, punteggio_totale) VALUES (?, 0)", (row_get(utente, 'id'),))
    db_commit(conn)
    giornate = db_fetchall(conn, "SELECT giornata FROM stato_giornata WHERE is_in_archivio = TRUE")
    for g in giornate:
        _calcola_punti_giornata_conn(row_get(g, 'giornata'), conn)
    db_commit(conn)
    conn.close()
    return "Classifica generale ricalcolata con successo."

def ricalcola_punteggi_finali():
    conn = get_db_connection()
    rf = db_fetchone(conn, "SELECT * FROM risultati_finali WHERE id = 1")
    if not rf or not row_get(rf, 'squadra_1'):
        conn.close()
        return "Errore: inserire prima i risultati reali di fine stagione."
    ricalcola_punteggi_totali()
    conn = get_db_connection()
    utenti = db_fetchall(conn, "SELECT id FROM utenti")
    for utente in utenti:
        uid = row_get(utente, 'id')
        pronostico = db_fetchone(conn, "SELECT * FROM pronostici_iniziali WHERE id_utente = ?", (uid,))
        if pronostico:
            punti = 0
            corrette = 0
            for i in range(1, 5):
                k = f'squadra_{i}'
                if (row_get(pronostico, k) or '').strip().lower() == (row_get(rf, k) or '').strip().lower():
                    punti += 20; corrette += 1
            if corrette == 4:
                punti += 10
            if (row_get(pronostico, 'capocannoniere') or '').strip().lower() == (row_get(rf, 'capocannoniere') or '').strip().lower():
                punti += 20
            db_execute(conn, "UPDATE punteggi SET punteggio_totale = punteggio_totale + ? WHERE id_utente = ?", (punti, uid))
    db_commit(conn)
    conn.close()
    return "Punti finali di stagione calcolati con successo!"


# ─────────────────────────────────────────────
# INTEGRAZIONE API FOOTBALL-DATA.ORG
# ─────────────────────────────────────────────

import requests as http_requests
from datetime import timezone

FOOTBALL_API_KEY = os.environ.get('FOOTBALL_API_KEY', '')
FOOTBALL_API_BASE = 'https://api.football-data.org/v4'
SERIE_A_CODE = 'SA'

# Config email
GMAIL_USER = os.environ.get('GMAIL_USER', '')
GMAIL_APP_PASSWORD = os.environ.get('GMAIL_APP_PASSWORD', '')
APP_URL = os.environ.get('APP_URL', 'https://fanta-serie-a-1.onrender.com')
RESEND_API_KEY = os.environ.get('RESEND_API_KEY', '')
API_FOOTBALL_KEY = os.environ.get('API_FOOTBALL_KEY', '')
API_FOOTBALL_BASE = 'https://v3.football.api-sports.io'
SERIE_A_ID = 135  # ID Serie A su API-Football
EMAIL_FROM_NAME = 'FantaSerieA'
EMAIL_FROM_ADDRESS = 'onboarding@resend.dev'  # Mittente verificato Resend gratuito

# Email config
EMAIL_FROM = os.environ.get('EMAIL_FROM', 'fantaseriea.notifiche@gmail.com')
EMAIL_PASSWORD = os.environ.get('EMAIL_PASSWORD', '')

def api_headers():
    return {'X-Auth-Token': FOOTBALL_API_KEY}



def build_email_pronostici(giornata, partite):
    """Costruisce il corpo HTML dell email di reminder pronostici."""
    partite_html = ''
    for p in partite:
        data_fmt = p['data_ora_partita'] if p['data_ora_partita'] else 'Data da definire'
        partite_html += f'''
        <tr>
          <td style="padding:12px 16px;border-bottom:1px solid #e8edf5;">
            <div style="font-weight:700;font-size:16px;color:#111d33;">{p['squadra_casa']} vs {p['squadra_ospite']}</div>
            <div style="font-size:13px;color:#7a8ba8;margin-top:2px;">{data_fmt}</div>
          </td>
        </tr>'''
    return f'''<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f0f4f8;font-family:Arial,sans-serif;">
  <div style="max-width:480px;margin:32px auto;background:white;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.08);">
    <div style="background:linear-gradient(135deg,#003f8a 0%,#0f4a1e 100%);padding:28px 24px;text-align:center;">
      <div style="font-size:36px;margin-bottom:8px;">🏆</div>
      <h1 style="color:white;margin:0;font-size:24px;letter-spacing:1px;">FantaSerieA</h1>
      <p style="color:rgba(255,255,255,0.8);margin:8px 0 0;font-size:14px;">Giornata {giornata} — Inserisci i tuoi pronostici!</p>
    </div>
    <div style="padding:24px;">
      <p style="color:#111d33;font-size:15px;margin:0 0 16px;">Ciao! Le partite della <strong>Giornata {giornata}</strong> sono pronte. Inserisci i tuoi pronostici prima del fischio d'inizio!</p>
      <table style="width:100%;border-collapse:collapse;background:#f8fafc;border-radius:10px;overflow:hidden;margin-bottom:24px;">
        {partite_html}
      </table>
      <div style="text-align:center;">
        <a href="{APP_URL}/pronostici-giornata/{giornata}"
          style="display:inline-block;background:linear-gradient(135deg,#1565c0,#0090d4);color:white;text-decoration:none;padding:14px 32px;border-radius:10px;font-weight:700;font-size:16px;letter-spacing:0.5px;">
          Inserisci i pronostici →
        </a>
      </div>
      <p style="color:#7a8ba8;font-size:12px;text-align:center;margin:20px 0 0;">Ricevi questa email perché sei registrato su FantaSerieA.<br>Per modificare le tue preferenze accedi al tuo profilo.</p>
    </div>
  </div>
</body>
</html>'''

def af_headers():
    return {
        'x-apisports-key': API_FOOTBALL_KEY
    }

def get_marcatori_partita_af(squadra_casa, squadra_ospite, data_ora_utc_str):
    """Scarica i marcatori di una partita da API-Football cercando per squadre e data."""
    if not API_FOOTBALL_KEY:
        return None, "API_FOOTBALL_KEY non configurata"
    try:
        # Estrai la data dalla stringa UTC
        if not data_ora_utc_str:
            return None, "Data partita mancante"
        orario_naive = parse_flexible_datetime(str(data_ora_utc_str))
        if not orario_naive:
            return None, "Formato data non valido"
        data_str = orario_naive.strftime('%Y-%m-%d')

        # Cerca fixtures per data e lega
        url = f"{API_FOOTBALL_BASE}/fixtures"
        # Serie A season: es. 2025-26 = season 2025 (inizia agosto 2025)
        # Se mese >= 8 (agosto-dicembre) → season = anno corrente
        # Se mese < 8 (gennaio-luglio) → season = anno precedente
        season = orario_naive.year if orario_naive.month >= 8 else orario_naive.year - 1
        params = {
            'league': SERIE_A_ID,
            'season': season,
            'date': data_str
        }
        r = http_requests.get(url, headers=af_headers(), params=params, timeout=10)
        if r.status_code != 200:
            return None, f"Errore API-Football ({r.status_code})"
        data = r.json()
        fixtures = data.get('response', [])
        print(f'[AF DEBUG] Status:{r.status_code} Season:{season} Date:{data_str} Fixtures:{len(fixtures)}', flush=True)
        if fixtures:
            [print(f'[AF DEBUG] Disponibile: {f["teams"]["home"]["name"]} vs {f["teams"]["away"]["name"]}', flush=True) for f in fixtures[:5]]
        if data.get('errors'):
            return None, f'Errore API: {data["errors"]}'

        # Cerca la partita corrispondente per nome squadra
        fixture_id = None
        for f in fixtures:
            home = f['teams']['home']['name'].upper()
            away = f['teams']['away']['name'].upper()
            sc = squadra_casa.upper()
            so = squadra_ospite.upper()
            if (sc in home or home in sc) and (so in away or away in so):
                fixture_id = f['fixture']['id']
                break

        if not fixture_id:
            return None, f"Partita {squadra_casa} vs {squadra_ospite} non trovata per {data_str}"

        # Scarica gli eventi della partita
        r2 = http_requests.get(f"{API_FOOTBALL_BASE}/fixtures/events",
            headers=af_headers(),
            params={'fixture': fixture_id},
            timeout=10)
        if r2.status_code != 200:
            return None, f"Errore eventi ({r2.status_code})"
        eventi = r2.json().get('response', [])

        # Filtra solo i gol (escludi autogol)
        marcatori = []
        for ev in eventi:
            if ev.get('type') == 'Goal' and ev.get('detail') != 'Own Goal':
                nome = ev.get('player', {}).get('name', '').strip()
                if nome:
                    marcatori.append(nome)

        return marcatori, None
    except Exception as e:
        return None, str(e)

def get_marcatori_giornata_af(giornata, partite_pronosticabili):
    """Scarica marcatori per le partite pronosticabili di una giornata. Usa 2 chiamate per partita."""
    risultati = {}
    errori = []
    for partita in partite_pronosticabili:
        pid = row_get(partita, 'id')
        sc = row_get(partita, 'squadra_casa')
        so = row_get(partita, 'squadra_ospite')
        dop = row_get(partita, 'data_ora_partita')
        marcatori, errore = get_marcatori_partita_af(sc, so, dop)
        if errore:
            errori.append(f"{sc} vs {so}: {errore}")
            risultati[pid] = None
        else:
            risultati[pid] = ', '.join(marcatori) if marcatori else ''
    return risultati, errori

def get_matches_giornata(giornata):
    """Scarica i match di una giornata di Serie A dall API."""
    url = f"{FOOTBALL_API_BASE}/competitions/{SERIE_A_CODE}/matches?matchday={giornata}"
    r = http_requests.get(url, headers=api_headers(), timeout=10)
    if r.status_code != 200:
        return None, f"Errore API ({r.status_code}): {r.text[:200]}"
    data = r.json()
    partite = []
    for m in data.get('matches', []):
        data_utc = m.get('utcDate', '')[:16].replace('T', 'T')
        partite.append({
            'id': m['id'],
            'home': m['homeTeam']['name'],
            'away': m['awayTeam']['name'],
            'home_id': m['homeTeam']['id'],
            'away_id': m['awayTeam']['id'],
            'data_ora': data_utc,
        })
    return partite, None

def get_giornata_corrente():
    """Restituisce la giornata corrente della Serie A."""
    url = f"{FOOTBALL_API_BASE}/competitions/{SERIE_A_CODE}"
    r = http_requests.get(url, headers=api_headers(), timeout=10)
    if r.status_code == 200:
        return r.json().get('currentSeason', {}).get('currentMatchday')
    return None

def get_giocatori_squadra(team_id, team_name):
    """Scarica la rosa di una squadra dall API."""
    url = f"{FOOTBALL_API_BASE}/teams/{team_id}"
    r = http_requests.get(url, headers=api_headers(), timeout=10)
    if r.status_code != 200:
        return []
    data = r.json()
    giocatori = []
    for p in data.get('squad', []):
        if p.get('position') != 'Goalkeeper':
            giocatori.append({
                'nome': p.get('name', ''),
                'squadra': team_name.upper()
            })
    return giocatori

def get_risultati_giornata(giornata):
    """Scarica risultati e marcatori reali di una giornata dall API."""
    url = f"{FOOTBALL_API_BASE}/competitions/{SERIE_A_CODE}/matches?matchday={giornata}"
    r = http_requests.get(url, headers=api_headers(), timeout=10)
    if r.status_code != 200:
        return None, f"Errore API ({r.status_code}): {r.text[:200]}"
    data = r.json()
    risultati = []
    for m in data.get('matches', []):
        if m.get('status') != 'FINISHED':
            continue
        home = m['homeTeam']['name'].upper()
        away = m['awayTeam']['name'].upper()
        score = m.get('score', {}).get('fullTime', {})
        gol_home = score.get('home')
        gol_away = score.get('away')
        # Debug: log prima partita per verificare campo goals
        if not risultati:
            print(f"[API DEBUG] Chiavi: {list(m.keys())}", flush=True)
            print(f"[API DEBUG] goals: {m.get('goals', 'ASSENTE')}", flush=True)
        # Estrai tutti i marcatori (escludi autogol come marcatori validi)
        marcatori = []
        for g in m.get('goals', []):
            tipo = g.get('type', 'REGULAR')
            if tipo == 'OWN':
                continue  # autogol non conta come marcatore pronosticabile
            scorer = g.get('scorer', {})
            nome = scorer.get('name', '').strip()
            if nome:
                marcatori.append(nome)
        risultati.append({
            'home': home,
            'away': away,
            'gol_home': gol_home,
            'gol_away': gol_away,
            'marcatori': marcatori,  # lista di tutti i marcatori reali
            'marcatori_str': ', '.join(marcatori),
            'status': m.get('status'),
        })
    return risultati, None

@app.route("/admin/importa-risultati/<int:giornata>", methods=["POST"])
def admin_importa_risultati(giornata):
    """Importa risultati e marcatori reali dall API per le partite pronosticabili."""
    if require_admin(): return "Accesso negato.", 403
    try:
        risultati_api, errore = get_risultati_giornata(giornata)
        if errore:
            flash(f"Errore API: {errore}", "danger")
            return redirect(url_for("admin_home"))
        if not risultati_api:
            flash(f"Nessuna partita terminata trovata per la giornata {giornata}.", "warning")
            return redirect(url_for("admin_home"))
        conn = get_db_connection()
        partite_db = db_fetchall(conn,
            "SELECT * FROM partite WHERE giornata = ?", (giornata,))
        aggiornate = 0
        non_trovate = []
        for partita in partite_db:
            sc = row_get(partita, 'squadra_casa').upper()
            so = row_get(partita, 'squadra_ospite').upper()
            # Cerca corrispondenza nell API (match parziale sul nome squadra)
            match_api = None
            for r in risultati_api:
                if (sc in r['home'] or r['home'] in sc) and (so in r['away'] or r['away'] in so):
                    match_api = r
                    break
            if match_api:
                db_execute(conn,
                    "UPDATE partite SET risultato_casa_reale=?, risultato_ospite_reale=?, marcatore_reale=? WHERE id=?",
                    (match_api['gol_home'], match_api['gol_away'],
                     match_api['marcatori_str'], row_get(partita, 'id')))
                aggiornate += 1
            else:
                non_trovate.append(f"{sc} vs {so}")
        db_commit(conn)
        conn.close()
        msg = f"Risultati importati: {aggiornate} partite aggiornate."
        if non_trovate:
            msg += f" Non trovate: {', '.join(non_trovate)} — aggiorna manualmente."
        flash(msg, "success" if not non_trovate else "warning")
    except Exception as e:
        flash(f"Errore durante l importazione: {str(e)}", "danger")
    return redirect(url_for("admin_home"))

@app.route("/admin/invia-reminder/<int:giornata>", methods=["POST"])
def admin_invia_reminder(giornata):
    if require_admin(): return "Accesso negato.", 403
    try:
        conn = get_db_connection()
        partite = db_fetchall(conn,
            "SELECT squadra_casa, squadra_ospite, data_ora_partita FROM partite WHERE giornata = ? AND pronosticabile = TRUE",
            (giornata,))
        utenti_email = db_fetchall(conn,
            "SELECT email FROM utenti WHERE email IS NOT NULL AND email != ''")
        conn.close()
        destinatari = [row_get(u, 'email') for u in utenti_email if row_get(u, 'email')]
        if not destinatari:
            flash("Nessun utente con email registrata.", "warning")
            return redirect(url_for('admin_home'))
        partite_list = [{'squadra_casa': row_get(p, 'squadra_casa'),
                         'squadra_ospite': row_get(p, 'squadra_ospite'),
                         'data_ora_partita': row_get(p, 'data_ora_partita')} for p in partite]
        html = build_email_giornata(giornata, partite_list)
        oggetto = f"⚽ FantaSerieA — Reminder Giornata {giornata}: inserisci i pronostici!"
        invia_email_async(destinatari, oggetto, html)
        flash(f"Reminder in invio a {len(destinatari)} utenti!", "success")
    except Exception as e:
        flash(f"Errore invio reminder: {str(e)}", "danger")
    return redirect(url_for('admin_home'))

@app.route("/admin/aggiorna-risultati-massivo", methods=["POST"])
def admin_aggiorna_risultati_massivo():
    """Lancia l aggiornamento massivo in background per evitare timeout gunicorn."""
    if require_admin(): return "Accesso negato.", 403

    def _esegui_massivo():
        import time
        print("[MASSIVO] Avvio aggiornamento storico risultati...", flush=True)
        try:
            conn = get_db_connection()
            giornate = db_fetchall(conn, "SELECT giornata FROM stato_giornata WHERE is_in_archivio = TRUE ORDER BY giornata")
            aggiornate = 0
            for i, g_row in enumerate(giornate):
                g = row_get(g_row, 'giornata')
                if i > 0 and i % 9 == 0:
                    print(f"[MASSIVO] Pausa rate limit dopo {i} chiamate...", flush=True)
                    time.sleep(62)
                try:
                    risultati_api, errore = get_risultati_giornata(g)
                    if errore or not risultati_api:
                        print(f"[MASSIVO] G{g} saltata: {errore or 'nessun risultato'}", flush=True)
                        continue
                    partite_db = db_fetchall(conn, "SELECT * FROM partite WHERE giornata = ?", (g,))
                    for partita in partite_db:
                        sc = row_get(partita, 'squadra_casa').upper()
                        so = row_get(partita, 'squadra_ospite').upper()
                        match_api = None
                        for r in risultati_api:
                            if (sc in r['home'] or r['home'] in sc) and (so in r['away'] or r['away'] in so):
                                match_api = r
                                break
                        if match_api:
                            db_execute(conn, "UPDATE partite SET risultato_casa_reale=?, risultato_ospite_reale=? WHERE id=?",
                                (match_api['gol_home'], match_api['gol_away'], row_get(partita, 'id')))
                    db_commit(conn)
                    # Importa marcatori da API-Football per le partite pronosticabili
                    if API_FOOTBALL_KEY:
                        partite_pron = db_fetchall(conn,
                            "SELECT * FROM partite WHERE giornata = ? AND pronosticabile = TRUE", (g,))
                        if partite_pron:
                            time.sleep(2)
                            marc_ris, marc_err = get_marcatori_giornata_af(g, partite_pron)
                            for pid_m, marc_str in marc_ris.items():
                                if marc_str is not None:
                                    db_execute(conn, "UPDATE partite SET marcatore_reale = ? WHERE id = ?",
                                        (marc_str, pid_m))
                            db_commit(conn)
                            if marc_err:
                                print(f"[MASSIVO] G{g} marcatori: {marc_err}", flush=True)
                    aggiornate += 1
                    print(f"[MASSIVO] G{g} aggiornata ({aggiornate}/{len(giornate)})", flush=True)
                    time.sleep(7)  # ~7s tra una chiamata e l'altra = max 8-9 req/min
                except Exception as e:
                    print(f"[MASSIVO] Errore G{g}: {e}", flush=True)
            conn.close()
            print(f"[MASSIVO] Completato: {aggiornate}/{len(giornate)} giornate aggiornate.", flush=True)
        except Exception as e:
            print(f"[MASSIVO] Errore generale: {e}", flush=True)

    t = threading.Thread(target=_esegui_massivo)
    t.daemon = True
    t.start()
    flash("Aggiornamento storico avviato in background. Controlla i log di Render per il progresso (~4 minuti).", "info")
    return redirect(url_for('admin_gestisci_partite'))

@app.route("/admin/importa-marcatori/<int:giornata>", methods=["POST"])
def admin_importa_marcatori(giornata):
    """Importa marcatori da API-Football per le partite pronosticabili della giornata."""
    if require_admin(): return "Accesso negato.", 403
    if not API_FOOTBALL_KEY:
        flash("API_FOOTBALL_KEY non configurata su Render.", "danger")
        return redirect(url_for('admin_home'))
    try:
        conn = get_db_connection()
        partite = db_fetchall(conn,
            "SELECT * FROM partite WHERE giornata = ? AND pronosticabile = TRUE", (giornata,))
        if not partite:
            conn.close()
            flash(f"Nessuna partita pronosticabile per giornata {giornata}.", "warning")
            return redirect(url_for('admin_home'))
        risultati, errori = get_marcatori_giornata_af(giornata, partite)
        aggiornate = 0
        for pid, marcatori_str in risultati.items():
            if marcatori_str is not None:
                db_execute(conn, "UPDATE partite SET marcatore_reale = ? WHERE id = ?",
                    (marcatori_str, pid))
                aggiornate += 1
        db_commit(conn)
        conn.close()
        msg = f"Marcatori importati: {aggiornate}/{len(partite)} partite aggiornate."
        if errori:
            msg += f" Errori: {'; '.join(errori[:2])}"
        flash(msg, "success" if not errori else "warning")
    except Exception as e:
        flash(f"Errore importazione marcatori: {str(e)}", "danger")
    return redirect(url_for('admin_home'))

@app.route("/admin/importa-giornata", methods=["GET", "POST"])
def admin_importa_giornata():
    if require_admin(): return "Accesso negato.", 403

    giornata_corrente = None
    partite_api = []
    giornata_selezionata = None
    errore = None
    selezionate = []

    try:
        giornata_corrente = get_giornata_corrente()
    except Exception:
        pass

    if request.method == "POST":
        azione = request.form.get('azione')
        giornata_selezionata = request.form.get('giornata', type=int)

        if azione == 'carica':
            try:
                partite_api, errore = get_matches_giornata(giornata_selezionata)
            except Exception as e:
                errore = f"Errore di connessione: {str(e)}"

        elif azione == 'salva':
            partita_ids = request.form.getlist('partita_ids', type=int)
            if not partita_ids:
                errore = "Seleziona almeno una partita."
            elif len(partita_ids) > 3:
                errore = "Puoi selezionare massimo 3 partite."
            else:
                try:
                    # Ricarica i dati API per avere i dettagli completi
                    partite_api, errore_api = get_matches_giornata(giornata_selezionata)
                    if errore_api:
                        errore = errore_api
                    else:
                        conn = get_db_connection()
                        partite_sel = [p for p in partite_api if p['id'] in partita_ids]

                        # Raccoglie tutti i team ID unici
                        team_ids = {}
                        for p in partite_sel:
                            team_ids[p['home_id']] = p['home'].upper()
                            team_ids[p['away_id']] = p['away'].upper()

                        # Inserisce le partite nel DB
                        for p in partite_sel:
                            home = p['home'].upper()
                            away = p['away'].upper()
                            data_ora = p['data_ora']
                            # Controlla se esiste già
                            esistente = db_fetchone(conn,
                                "SELECT id FROM partite WHERE giornata=? AND squadra_casa=? AND squadra_ospite=?",
                                (giornata_selezionata, home, away))
                            if esistente:
                                db_execute(conn,
                                    "UPDATE partite SET pronosticabile=TRUE, data_ora_partita=? WHERE id=?",
                                    (data_ora, row_get(esistente, 'id')))
                            else:
                                db_execute(conn,
                                    "INSERT INTO partite (giornata, squadra_casa, squadra_ospite, pronosticabile, data_ora_partita) VALUES (?,?,?,TRUE,?)",
                                    (giornata_selezionata, home, away, data_ora))

                        # Rimuove giocatori vecchi delle squadre coinvolte
                        for team_name in team_ids.values():
                            db_execute(conn, "DELETE FROM giocatori WHERE UPPER(squadra) = UPPER(?)", (team_name,))

                        # Importa giocatori aggiornati
                        for team_id, team_name in team_ids.items():
                            giocatori = get_giocatori_squadra(team_id, team_name)
                            for g in giocatori:
                                db_execute(conn,
                                    "INSERT INTO giocatori (nome_giocatore, squadra) VALUES (?,?)",
                                    (g['nome'], g['squadra']))

                        # Assicura che la giornata esista in stato_giornata come attiva
                        if USE_POSTGRES:
                            db_execute(conn,
                                "INSERT INTO stato_giornata (giornata, is_attiva, is_in_archivio) VALUES (?,TRUE,FALSE) ON CONFLICT (giornata) DO UPDATE SET is_attiva=TRUE",
                                (giornata_selezionata,))
                        else:
                            db_execute(conn,
                                "INSERT OR IGNORE INTO stato_giornata (giornata, is_attiva, is_in_archivio) VALUES (?,1,0)",
                                (giornata_selezionata,))
                            db_execute(conn,
                                "UPDATE stato_giornata SET is_attiva=1 WHERE giornata=?",
                                (giornata_selezionata,))

                        db_commit(conn)

                        # Invia email di notifica a tutti gli utenti con email registrata
                        partite_per_email = db_fetchall(conn,
                            "SELECT * FROM partite WHERE giornata = ? AND pronosticabile = TRUE ORDER BY data_ora_partita",
                            (giornata_selezionata,))
                        conn.close()
                        email_rows = get_db_connection()
                        destinatari_rows = db_fetchall(email_rows,
                            "SELECT email FROM utenti WHERE email IS NOT NULL AND email != ''")
                        email_rows.close()
                        destinatari = [row_get(r, 'email') for r in destinatari_rows if row_get(r, 'email')]
                        if destinatari:
                            corpo = build_email_giornata(giornata_selezionata, [dict(p) if hasattr(p, 'keys') else p for p in partite_per_email])
                            oggetto = f"🏆 FantaSerieA — Giornata {giornata_selezionata}: inserisci i tuoi pronostici!"
                            invia_email_async(destinatari, oggetto, corpo)
                            msg_email = f" Email in invio a {len(destinatari)} utenti."
                        else:
                            msg_email = " Nessun utente con email registrata."
                        session['flash_message'] = f"Giornata {giornata_selezionata}: {len(partite_sel)} partite importate.{msg_email}"
                        return redirect(url_for('admin_home'))
                except Exception as e:
                    errore = f"Errore durante il salvataggio: {str(e)}"

    return render_template('admin_importa_giornata.html',
        giornata_corrente=giornata_corrente,
        giornata_selezionata=giornata_selezionata,
        partite_api=partite_api,
        selezionate=selezionate,
        errore=errore,
        session=session)

# ─────────────────────────────────────────────
# ROUTE PROFILO UTENTE
# ─────────────────────────────────────────────

@app.route("/api/profilo-info")
def api_profilo_info():
    if 'nome_utente' not in session:
        return {'email': None}
    conn = get_db_connection()
    user = db_fetchone(conn, "SELECT email FROM utenti WHERE nome_utente = ?", (session['nome_utente'],))
    conn.close()
    return {'email': row_get(user, 'email') if user else None}

@app.route("/profilo", methods=["GET", "POST"])
def profilo():
    if 'nome_utente' not in session:
        return redirect(url_for('login'))
    conn = get_db_connection()
    user = db_fetchone(conn, "SELECT * FROM utenti WHERE nome_utente = ?", (session['nome_utente'],))
    email_attuale = row_get(user, 'email') if user else None

    if request.method == "POST":
        azione = request.form.get('azione')
        if azione == 'email':
            nuova_email = request.form.get('email', '').strip()
            if nuova_email:
                db_execute(conn, "UPDATE utenti SET email = ? WHERE nome_utente = ?", (nuova_email, session['nome_utente']))
                db_commit(conn)
                flash("Email aggiornata con successo!", "success")
            conn.close()
            return redirect(url_for('profilo'))
        elif azione == 'password':
            nuova_pw = request.form.get('nuova_password', '')
            conferma = request.form.get('conferma_password', '')
            if nuova_pw != conferma:
                conn.close()
                return render_template('profilo.html', email=email_attuale, session=session, errore="Le password non coincidono.")
            pw_hash = hashlib.sha256(nuova_pw.encode()).hexdigest()
            db_execute(conn, "UPDATE utenti SET password = ?, is_temp_password = FALSE WHERE nome_utente = ?", (pw_hash, session['nome_utente']))
            db_commit(conn)
            conn.close()
            flash("Password aggiornata con successo!", "success")
            return redirect(url_for('profilo'))

    conn.close()
    return render_template('profilo.html', email=email_attuale, session=session)

# ─────────────────────────────────────────────
# ROUTE ADMIN
# ─────────────────────────────────────────────

def require_admin():
    return 'nome_utente' not in session or session['nome_utente'] != 'mirko'

@app.route("/admin")
def admin_home():
    if require_admin(): return "Accesso negato.", 403
    conn = get_db_connection()
    giornata_attiva = db_fetchone(conn, "SELECT giornata FROM stato_giornata WHERE is_attiva = TRUE")
    partite_attive = []
    pronostici_inseriti = 0
    if giornata_attiva:
        g = row_get(giornata_attiva, 'giornata')
        partite_attive = db_fetchall(conn, "SELECT * FROM partite WHERE giornata = ? AND pronosticabile = TRUE", (g,))
        row_pi = db_fetchone(conn, "SELECT COUNT(*) as cnt FROM pronostici_giornata WHERE id_partita IN (SELECT id FROM partite WHERE giornata = ?)", (g,))
        pronostici_inseriti = row_get(row_pi, 'cnt') or 0
    # Carica giocatori per ogni partita attiva (per lista marcatori)
    giocatori_per_partita = {}
    for partita in partite_attive:
        pid = row_get(partita, 'id')
        sc = (row_get(partita, 'squadra_casa') or '').upper()
        so = (row_get(partita, 'squadra_ospite') or '').upper()
        giocatori_per_partita[pid] = db_fetchall(conn,
            "SELECT nome_giocatore, squadra FROM giocatori WHERE UPPER(squadra) = ? OR UPPER(squadra) = ? ORDER BY squadra, nome_giocatore",
            (sc, so))
    flash_message = session.pop('flash_message', None)
    conn.close()
    return render_template("admin.html", giornata_attiva=giornata_attiva, partite_attive=partite_attive,
        pronostici_inseriti=pronostici_inseriti, flash_message=flash_message,
        giocatori_per_partita=giocatori_per_partita, session=session)

@app.route("/admin/email-utenti", methods=["GET", "POST"])
def admin_email_utenti():
    if require_admin(): return "Accesso negato.", 403
    conn = get_db_connection()
    if request.method == "POST":
        utenti = db_fetchall(conn, "SELECT id FROM utenti")
        for utente in utenti:
            uid = row_get(utente, 'id')
            email = request.form.get(f"email_{uid}", "").strip()
            if email:
                db_execute(conn, "UPDATE utenti SET email = ? WHERE id = ?", (email, uid))
        db_commit(conn)
        conn.close()
        flash("Email utenti aggiornate con successo!", "success")
        return redirect(url_for('admin_email_utenti'))
    utenti = db_fetchall(conn, "SELECT id, nome_utente, email FROM utenti ORDER BY nome_utente")
    conn.close()
    return render_template("admin_email_utenti.html", utenti=utenti, session=session)

@app.route("/admin/utenti")
def admin_utenti():
    if require_admin(): return "Accesso negato.", 403
    conn = get_db_connection()
    utenti = db_fetchall(conn, "SELECT id, nome_utente, is_temp_password FROM utenti")
    conn.close()
    return render_template("admin_utenti.html", utenti=utenti, session=session)

@app.route("/admin/resetta-password/<int:id_utente>")
def admin_resetta_password(id_utente):
    if require_admin(): return "Accesso negato.", 403
    pw_temp = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    pw_hash = hashlib.sha256(pw_temp.encode()).hexdigest()
    conn = get_db_connection()
    db_execute(conn, "UPDATE utenti SET password = ?, is_temp_password = TRUE WHERE id = ?", (pw_hash, id_utente))
    db_commit(conn)
    utente = db_fetchone(conn, "SELECT nome_utente FROM utenti WHERE id = ?", (id_utente,))
    conn.close()
    nome = row_get(utente, 'nome_utente') if utente else 'Utente'
    flash(f"Password temporanea per {nome}: {pw_temp}", "success")
    return redirect(url_for('admin_utenti'))

@app.route("/admin/elimina-utente/<int:id_utente>")
def admin_elimina_utente(id_utente):
    if require_admin(): return "Accesso negato.", 403
    conn = get_db_connection()
    utente = db_fetchone(conn, "SELECT nome_utente FROM utenti WHERE id = ?", (id_utente,))
    if utente and row_get(utente, 'nome_utente') == 'mirko':
        conn.close()
        return redirect(url_for('admin_utenti'))
    db_execute(conn, "DELETE FROM punteggi WHERE id_utente = ?", (id_utente,))
    db_execute(conn, "DELETE FROM pronostici_giornata WHERE id_utente = ?", (id_utente,))
    db_execute(conn, "DELETE FROM pronostici_iniziali WHERE id_utente = ?", (id_utente,))
    db_execute(conn, "DELETE FROM utenti WHERE id = ?", (id_utente,))
    db_commit(conn)
    conn.close()
    return redirect(url_for('admin_utenti'))

@app.route("/admin/gestisci-partite")
def admin_gestisci_partite():
    if require_admin(): return "Accesso negato.", 403
    conn = get_db_connection()
    giornata_sel = request.args.get('giornata', type=int)
    giornate_rows = db_fetchall(conn, "SELECT DISTINCT giornata FROM partite ORDER BY giornata")
    giornate_disponibili = [row_get(r, 'giornata') for r in giornate_rows]
    if giornata_sel:
        partite = db_fetchall(conn, "SELECT * FROM partite WHERE giornata = ? ORDER BY data_ora_partita", (giornata_sel,))
    else:
        partite = db_fetchall(conn, "SELECT * FROM partite ORDER BY giornata, data_ora_partita")
    # Carica partite attive e giocatori per la sezione risultati
    giornata_attiva_row = db_fetchone(conn, "SELECT giornata FROM stato_giornata WHERE is_attiva = TRUE")
    partite_attive = []
    giocatori_per_partita = {}
    giornata_attiva_dict = None
    if giornata_attiva_row:
        g = row_get(giornata_attiva_row, 'giornata')
        giornata_attiva_dict = {'giornata': g}
        partite_attive = db_fetchall(conn, "SELECT * FROM partite WHERE giornata = ? AND pronosticabile = TRUE", (g,))
        for partita in partite_attive:
            pid = row_get(partita, 'id')
            sc = (row_get(partita, 'squadra_casa') or '').upper()
            so = (row_get(partita, 'squadra_ospite') or '').upper()
            giocatori_per_partita[pid] = db_fetchall(conn,
                "SELECT nome_giocatore, squadra FROM giocatori WHERE UPPER(squadra) = ? OR UPPER(squadra) = ? ORDER BY squadra, nome_giocatore",
                (sc, so))
    conn.close()
    return render_template("admin_gestisci_partite.html", tutte_le_partite=partite,
        giornate_disponibili=giornate_disponibili, giornata_selezionata=giornata_sel,
        giornata_attiva=giornata_attiva_dict, partite_attive=partite_attive,
        giocatori_per_partita=giocatori_per_partita, session=session)

@app.route("/admin/aggiungi-partita", methods=["POST"])
def aggiungi_partita():
    if require_admin(): return "Accesso negato.", 403
    conn = get_db_connection()
    db_execute(conn, "INSERT INTO partite (giornata, squadra_casa, squadra_ospite, pronosticabile, data_ora_partita) VALUES (?,?,?,?,?)",
               (request.form["giornata"], request.form["squadra_casa"].upper(), request.form["squadra_ospite"].upper(),
                request.form.get("pronosticabile") == "on", request.form["data_ora_partita"]))
    db_commit(conn)
    conn.close()
    return redirect(url_for("admin_gestisci_partite"))

@app.route("/admin/modifica-partita/<int:id_partita>", methods=["POST"])
def admin_modifica_partita(id_partita):
    if require_admin(): return "Accesso negato.", 403
    conn = get_db_connection()
    db_execute(conn, "UPDATE partite SET giornata=?, squadra_casa=?, squadra_ospite=?, pronosticabile=?, data_ora_partita=? WHERE id=?",
               (request.form["giornata"], request.form["squadra_casa"].upper(), request.form["squadra_ospite"].upper(),
                request.form.get("pronosticabile") == "on", request.form["data_ora_partita"], id_partita))
    db_commit(conn)
    conn.close()
    return redirect(url_for("admin_gestisci_partite", giornata=request.args.get('giornata')))

@app.route("/admin/elimina-partita/<int:id_partita>")
def admin_elimina_partita(id_partita):
    if require_admin(): return "Accesso negato.", 403
    conn = get_db_connection()
    db_execute(conn, "DELETE FROM partite WHERE id = ?", (id_partita,))
    db_commit(conn)
    conn.close()
    return redirect(url_for("admin_gestisci_partite", giornata=request.args.get('giornata')))

@app.route("/admin/risultati-giornata/<int:giornata>", methods=["POST"])
def admin_risultati_giornata(giornata):
    if require_admin(): return "Accesso negato.", 403
    conn = get_db_connection()
    partite = db_fetchall(conn, "SELECT * FROM partite WHERE giornata = ? AND pronosticabile = TRUE", (giornata,))
    for partita in partite:
        pid = row_get(partita, 'id')
        # Converti stringhe vuote in None per i campi interi (PostgreSQL non accetta '')
        r_casa_raw = request.form.get(f"risultato_casa_{pid}", "").strip()
        r_osp_raw = request.form.get(f"risultato_ospite_{pid}", "").strip()
        r_casa = int(r_casa_raw) if r_casa_raw else None
        r_osp = int(r_osp_raw) if r_osp_raw else None
        # Marcatore: solo dropdown singolo (1 marcatore per partita)
        marcatore = request.form.get(f"marcatore_{pid}", "").strip() or None
        db_execute(conn, "UPDATE partite SET risultato_casa_reale=?, risultato_ospite_reale=?, marcatore_reale=? WHERE id=?", (r_casa, r_osp, marcatore, pid))
    db_commit(conn)
    conn.close()
    flash("Risultati salvati con successo!", "success")
    return redirect(url_for("admin_home"))

@app.route("/admin/archivia-giornata/<int:giornata>")
def archivia_giornata(giornata):
    if require_admin(): return "Accesso negato.", 403
    conn = get_db_connection()
    db_execute(conn, "UPDATE stato_giornata SET is_attiva = FALSE, is_in_archivio = TRUE WHERE giornata = ?", (giornata,))
    prossima = giornata + 1
    if USE_POSTGRES:
        db_execute(conn, "INSERT INTO stato_giornata (giornata, is_attiva) VALUES (?, TRUE) ON CONFLICT (giornata) DO UPDATE SET is_attiva = TRUE", (prossima,))
    else:
        db_execute(conn, "INSERT OR IGNORE INTO stato_giornata (giornata, is_attiva) VALUES (?, 1)", (prossima,))
        db_execute(conn, "UPDATE stato_giornata SET is_attiva = 1 WHERE giornata = ?", (prossima,))
    db_commit(conn)
    conn.close()
    return redirect(url_for("admin_home"))

@app.route("/admin/calcola-punti-giornata/<int:giornata>")
def admin_calcola_punti_giornata(giornata):
    if require_admin(): return "Accesso negato.", 403
    session['flash_message'] = calcola_e_aggiorna_punti_giornata(giornata)
    return redirect(url_for("admin_home"))

@app.route("/calcola-punteggi")
def calcola_punteggi():
    if require_admin(): return "Accesso negato.", 403
    session['flash_message'] = ricalcola_punteggi_totali()
    return redirect(url_for("admin_home"))

@app.route("/admin/gestisci-pronostici/<int:giornata>", methods=["GET", "POST"])
def admin_gestisci_pronostici(giornata):
    if require_admin(): return "Accesso negato.", 403
    conn = get_db_connection()
    if request.method == "POST" and request.form.get('action') == 'modifica':
        pid = request.form.get('id_pronostico')
        db_execute(conn, "UPDATE pronostici_giornata SET esito_pronosticato=?, risultato_casa_pronosticato=?, risultato_ospite_pronosticato=?, marcatore_pronosticato=? WHERE id=?",
                   (request.form.get('esito'), request.form.get('risultato_casa'), request.form.get('risultato_ospite'), request.form.get('marcatore'), pid))
        db_commit(conn)
        conn.close()
        return redirect(url_for('admin_gestisci_pronostici', giornata=giornata))
    if request.args.get('action') == 'cancella' and request.args.get('id_pronostico'):
        db_execute(conn, "DELETE FROM pronostici_giornata WHERE id = ?", (request.args.get('id_pronostico'),))
        db_commit(conn)
        conn.close()
        return redirect(url_for('admin_gestisci_pronostici', giornata=giornata))
    partite = db_fetchall(conn, "SELECT * FROM partite WHERE giornata = ? AND pronosticabile = TRUE", (giornata,))
    pronostici_per_partita = {}
    for partita in partite:
        pid = row_get(partita, 'id')
        pronostici_per_partita[pid] = db_fetchall(conn, "SELECT u.nome_utente, pg.* FROM pronostici_giornata pg JOIN utenti u ON pg.id_utente = u.id WHERE pg.id_partita = ?", (pid,))
    conn.close()
    return render_template('admin_gestisci_pronostici.html', giornata=giornata, partite=partite, pronostici_per_partita=pronostici_per_partita, session=session)

@app.route("/admin/gestisci-pronostici-iniziali")
def admin_gestisci_pronostici_iniziali():
    if require_admin(): return "Accesso negato.", 403
    conn = get_db_connection()
    pronostici = db_fetchall(conn, "SELECT u.nome_utente, pi.* FROM utenti u JOIN pronostici_iniziali pi ON u.id = pi.id_utente")
    lock_row = db_fetchone(conn, "SELECT is_locked FROM stato_pronostici_iniziali WHERE id = 1")
    is_locked = row_get(lock_row, 'is_locked') if lock_row else False
    conn.close()
    return render_template("admin_gestisci_pronostici_iniziali.html", pronostici=pronostici, is_locked=is_locked, session=session)

@app.route("/admin/elimina-pronostico-iniziale/<int:id_pronostico>")
def admin_elimina_pronostico_iniziale(id_pronostico):
    if require_admin(): return "Accesso negato.", 403
    conn = get_db_connection()
    db_execute(conn, "DELETE FROM pronostici_iniziali WHERE id = ?", (id_pronostico,))
    db_commit(conn)
    conn.close()
    return redirect(url_for('admin_gestisci_pronostici_iniziali'))

@app.route("/admin/gestisci-finalizzazione")
def admin_gestisci_finalizzazione():
    if require_admin(): return "Accesso negato.", 403
    conn = get_db_connection()
    lock_row = db_fetchone(conn, "SELECT is_locked FROM stato_pronostici_iniziali WHERE id = 1")
    is_locked = row_get(lock_row, 'is_locked') if lock_row else False
    conn.close()
    return render_template("admin_finalizzazione.html", is_locked=is_locked, session=session)


@app.route("/admin/gestisci-email", methods=["GET", "POST"])
def admin_gestisci_email():
    if require_admin(): return "Accesso negato.", 403
    conn = get_db_connection()
    if request.method == "POST":
        utenti = db_fetchall(conn, "SELECT id, nome_utente FROM utenti")
        aggiornati = 0
        for utente in utenti:
            uid = row_get(utente, 'id')
            nome = row_get(utente, 'nome_utente')
            email = request.form.get(f"email_{uid}", "").strip().lower()
            if email and '@' in email:
                db_execute(conn, "UPDATE utenti SET email = ? WHERE id = ?", (email, uid))
                aggiornati += 1
        db_commit(conn)
        conn.close()
        flash(f"Email aggiornate per {aggiornati} utenti.", "success")
        return redirect(url_for('admin_gestisci_email'))
    utenti = db_fetchall(conn, "SELECT id, nome_utente, email FROM utenti ORDER BY nome_utente")
    conn.close()
    return render_template('admin_gestisci_email.html', utenti=utenti, session=session)

@app.route("/admin/blocca-pronostici-iniziali", methods=["POST"])
def blocca_pronostici_iniziali():
    if require_admin(): return "Accesso negato.", 403
    conn = get_db_connection()
    db_execute(conn, "UPDATE stato_pronostici_iniziali SET is_locked = TRUE WHERE id = 1")
    db_commit(conn)
    conn.close()
    return redirect(url_for('admin_gestisci_finalizzazione'))

@app.route("/admin/sblocca-pronostici-iniziali", methods=["POST"])
def sblocca_pronostici_iniziali():
    if require_admin(): return "Accesso negato.", 403
    conn = get_db_connection()
    db_execute(conn, "UPDATE stato_pronostici_iniziali SET is_locked = FALSE WHERE id = 1")
    db_commit(conn)
    conn.close()
    return redirect(url_for('admin_gestisci_finalizzazione'))

@app.route("/admin/calcola-punti-finali", methods=["GET", "POST"])
def admin_calcola_punti_finali():
    if require_admin(): return "Accesso negato.", 403
    conn = get_db_connection()
    messaggio = None
    if request.method == 'POST':
        db_execute(conn, "UPDATE risultati_finali SET squadra_1=?, squadra_2=?, squadra_3=?, squadra_4=?, capocannoniere=? WHERE id=1",
                   (request.form['squadra_1'], request.form['squadra_2'], request.form['squadra_3'], request.form['squadra_4'], request.form['capocannoniere']))
        db_commit(conn)
        messaggio = ricalcola_punteggi_finali()
    rf = db_fetchone(conn, "SELECT * FROM risultati_finali WHERE id = 1")
    conn.close()
    return render_template("admin_calcola_punti_finali.html", risultati_finali=rf, messaggio=messaggio, session=session)

@app.route("/admin/modifica-giornata-archiviata/<int:giornata>", methods=["GET", "POST"])
def admin_modifica_giornata_archiviata(giornata):
    if require_admin(): return "Accesso negato.", 403
    conn = get_db_connection()
    if request.method == "POST":
        partite = db_fetchall(conn, "SELECT * FROM partite WHERE giornata = ? AND pronosticabile = TRUE", (giornata,))
        for partita in partite:
            pid = row_get(partita, 'id')
            r_casa_raw = request.form.get(f"risultato_casa_{pid}", "").strip()
            r_osp_raw = request.form.get(f"risultato_ospite_{pid}", "").strip()
            r_casa = int(r_casa_raw) if r_casa_raw else None
            r_osp = int(r_osp_raw) if r_osp_raw else None
            marcatore = request.form.get(f"marcatore_{pid}", "").strip() or None
            db_execute(conn, "UPDATE partite SET risultato_casa_reale=?, risultato_ospite_reale=?, marcatore_reale=? WHERE id=?",
                (r_casa, r_osp, marcatore, pid))
        db_commit(conn)
        conn.close()
        flash(f"Risultati giornata {giornata} aggiornati.", "success")
        return redirect(url_for('admin_modifica_giornata_archiviata', giornata=giornata))
    partite = db_fetchall(conn, "SELECT * FROM partite WHERE giornata = ? AND pronosticabile = TRUE ORDER BY data_ora_partita", (giornata,))
    giocatori_per_partita = {}
    for partita in partite:
        pid = row_get(partita, 'id')
        sc = (row_get(partita, 'squadra_casa') or '').upper()
        so = (row_get(partita, 'squadra_ospite') or '').upper()
        giocatori_per_partita[pid] = db_fetchall(conn,
            "SELECT nome_giocatore, squadra FROM giocatori WHERE UPPER(squadra) = ? OR UPPER(squadra) = ? ORDER BY squadra, nome_giocatore",
            (sc, so))
    conn.close()
    return render_template("admin_modifica_giornata_archiviata.html",
        giornata=giornata, partite=partite,
        giocatori_per_partita=giocatori_per_partita, session=session)

@app.route("/admin/ricalcola-tutto")
def admin_ricalcola_tutta_la_classifica():
    if require_admin(): return "Accesso negato.", 403
    session['flash_message'] = ricalcola_punteggi_totali()
    return redirect(url_for("admin_home"))

# ─────────────────────────────────────────────
# AVVIO
# ─────────────────────────────────────────────

if __name__ == "__main__":
    create_tables()
    app.run(debug=True)

# Esegui create_tables all'avvio anche con gunicorn
with app.app_context():
    try:
        create_tables()
    except Exception as e:
        print(f"ERRORE create_tables: {e}", flush=True)
