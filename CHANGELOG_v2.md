# Changelog v2 — Sicurezza, logica di gioco, pulizia

Questo documento riassume cosa è cambiato dalla v1, **cosa devi fare** dopo
l'aggiornamento, e cosa è stato deliberatamente rimandato.

---

## 🔒 Sicurezza

### Password — hashing PBKDF2 con migrazione trasparente
- Prima: `hashlib.sha256(pw).hexdigest()` (no salt, vulnerabile a rainbow table).
- Adesso: `werkzeug.security.generate_password_hash` (PBKDF2-SHA256, salt 16 byte).
- **Migrazione trasparente**: al primo login con la vecchia password, l'hash
  viene riconosciuto come SHA-256 "legacy", verificato, e **subito riconvertito**
  al nuovo formato. L'utente non si accorge di nulla.
- Nuova validazione: password ≥ 6 caratteri (modificabile via `MIN_PASSWORD_LEN`).

### Admin — flag in DB invece di username hardcoded
- Prima: `require_admin()` controllava `session['nome_utente'] != 'mirko'`.
- Adesso: colonna `is_admin BOOLEAN` su `utenti`. La migrazione automatica
  promuove `mirko` ad admin se nessun admin esiste (compatibilità).
- Per cambiare l'utente "legacy" che viene promosso, imposta la env var
  `LEGACY_ADMIN_USERNAME`.

### CSRF + tutte le mutazioni in POST
- Aggiunto `Flask-WTF` (`CSRFProtect`).
- Token CSRF inserito in tutti i form esistenti.
- Le route distruttive che erano GET (`/admin/elimina-utente`, `/admin/resetta-password`,
  `/admin/archivia-giornata`, `/admin/calcola-punti-giornata`, `/calcola-punteggi`,
  `/admin/elimina-pronostico-iniziale`, `/admin/elimina-partita`,
  `/admin/ricalcola-tutta-la-classifica`) ora **richiedono POST + token CSRF**.
- I template che linkavano queste route sono stati convertiti in mini-form con
  bottone (mantenendo lo stesso aspetto grafico).

### Rate limit
- Aggiunto `Flask-Limiter`:
  - login: **5 tentativi/minuto e 30/ora per IP**
  - registrazione: **10/ora per IP**

### Secret key — niente fallback debole
- Prima: fallback hardcoded a `"chiave_segreta_molto_segreta"` se la env var mancava.
- Adesso: in produzione (rilevata via `DATABASE_URL` o `RENDER`), se manca
  `SECRET_KEY` **l'app si rifiuta di partire**. In locale viene generata una
  volta e persistita in `.local_secret_key` (gitignored).

### Cookie di sessione
- Impostati `HttpOnly` e `SameSite=Lax` espliciti.
- `Secure` attivato automaticamente quando l'app è in produzione (HTTPS).

### API key Football-Data
- Rimossa dagli script `scripts/importa_calendario.py` e `scripts/importa_da_api.py`.
- Ora letta da env var `FOOTBALL_API_KEY`. **Revoca la vecchia chiave** se è mai
  finita altrove e generane una nuova.

---

## 🎮 Logica di gioco

### Idempotenza del calcolo punti
- Prima: `calcola_e_aggiorna_punti_giornata` sommava sopra al totale esistente.
  Cliccare due volte = punti doppi.
- Adesso: nuova tabella `punteggi_giornata(id_utente, giornata, punti)` con
  vincolo `UNIQUE(id_utente, giornata)`. UPSERT idempotente.
  `punteggi.punteggio_totale` resta come somma cached, ricalcolata ad ogni
  aggiornamento di giornata.
- Conseguenza: puoi ricalcolare quante volte vuoi senza sballare la classifica.

### Bonus stagione idempotente
- `ricalcola_punteggi_finali` prima sommava sopra ai totali esistenti.
- Adesso prima ricostruisce i totali da zero (con `ricalcola_punteggi_totali`),
  poi somma i bonus della stagione. Eseguirlo 10 volte = stesso risultato.

### Formula punti — unica fonte di verità
- Estratta `calcola_punti_pronostico(pronostico, partita) -> dict`.
- Usata sia dalla pagina `/giornata/<n>` (live) sia dal calcolo persistente.
- Modificare la formula in un solo posto.
- Costanti `PUNTI_ESITO=1`, `PUNTI_RISULTATO=3`, `PUNTI_MARCATORE=2`,
  `PUNTI_BONUS_TRIPLA=1` in cima al modulo per renderle modificabili.

### Validazione input
- Risultati: `_safe_int` con range `0–20` (niente più stringhe vuote o negativi salvati).
- Email: regex `[^@\s]+@[^@\s]+\.[^@\s]+` su tutti i punti d'ingresso.
- Giornata: range `1–50`.
- Password: lunghezza minima.

### N+1 ridotto sul calcolo punti
- Il calcolo della giornata ora pre-carica tutti i pronostici della giornata
  in una sola query (`WHERE id_partita IN (...)`) invece di una per coppia
  utente×partita. Risparmio enorme con 20 utenti × 3 partite.
- Stesso approccio in `/giornata/<n>` (visualizzazione).

---

## 🧹 Pulizia

- **Connessioni DB**: ora gestite con context manager (`with db_conn() as conn:`)
  → niente più leak su eccezioni.
- **Eccezioni mute** sostituite con `log.exception(...)` (logging strutturato).
- **Codice morto rimosso**: `import smtplib`, doppio import `MIMEMultipart`.
- **`app.run(debug=True)`** controllato da env var `FLASK_DEBUG=1`.
- **Script di utility** spostati da root a `scripts/` con README dedicato.
- **`.gitignore`** aggiunto.
- **render.yaml** aggiornato con le env vars necessarie.

---

## 🚦 Cosa devi fare DOPO aver aggiornato

### Su Render (produzione)

1. **Imposta `FOOTBALL_API_KEY` e `RESEND_API_KEY`** come env vars (sync: false).
2. `SECRET_KEY` viene generata automaticamente al primo deploy (vedi `render.yaml`).
3. Al primo avvio l'app esegue automaticamente:
   - migrazione schema (`ALTER TABLE … ADD COLUMN is_admin`)
   - creazione tabella `punteggi_giornata`
   - promozione di `mirko` ad admin
4. **Verifica che la promozione admin sia avvenuta** (login con mirko, controlla
   che vedi il menu admin). Se non funziona, esegui manualmente sul DB:
   ```sql
   UPDATE utenti SET is_admin = TRUE WHERE nome_utente = 'mirko';
   ```
5. **Ricalcola la classifica una volta** dalla dashboard admin → "Ricalcola tutto".
   Questo popola la nuova tabella `punteggi_giornata` dai dati delle giornate
   archiviate. Il totale precedente potrebbe differire leggermente se in passato
   è stato cliccato per sbaglio "Calcola punteggi" due volte sulla stessa giornata
   — adesso sarà finalmente corretto.

### In locale

1. `pip install -r requirements.txt` (per Flask-WTF e Flask-Limiter).
2. Lancia `python app.py`. La SECRET_KEY locale viene generata automaticamente.
3. Per gli script in `scripts/`: `export FOOTBALL_API_KEY="..."` prima di lanciarli.

### Lato utenti

**Nessuna azione richiesta**: gli hash password vecchi (SHA-256) sono riconosciuti
al login e convertiti in background al nuovo formato. Trasparente.

---

## ⏭ Rimandato (consigliato per la v3)

Cose che ho lasciato fuori perché meglio affrontarle in iterazione successiva
con piano dedicato:

- **Refactor in Blueprint** (`auth.py`, `admin.py`, `pronostici.py`, `email.py`):
  `app.py` resta monolitico ma più pulito. ~1700 righe.
- **Unificazione schema DB** SQLite/PostgreSQL: ora restano due funzioni separate.
  Soluzione corretta sarebbe SQLAlchemy + Alembic per le migrazioni.
- **N+1 residue** in altre route (es. admin gestisci partite carica giocatori in
  loop). Il calcolo punti è già stato ottimizzato.
- **CSS in file statico** invece di inline in `base.html` (~800 righe).
- **Header di sicurezza** (CSP, X-Frame-Options, HSTS) con `flask-talisman`.
- **Test automatici**: lo script di gioco si presta bene a pytest.
