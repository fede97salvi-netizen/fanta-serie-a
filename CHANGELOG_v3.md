# Changelog v3 — Blueprint, SQLAlchemy, CSS statico, Talisman, Test

## Struttura

### Refactor Blueprint
`app.py` da 1900 righe è ora una factory `create_app()` di ~200 righe.
Le route sono suddivise in:
- `blueprints/auth.py`  — login, registrazione, profilo, cambio password
- `blueprints/gioco.py` — classifica, archivio, pronostici, giornata
- `blueprints/admin.py` — tutto il pannello /admin/*

I servizi condivisi sono estratti in:
- `services/game_logic.py`  — logica punti, calcolo, ricalcolo
- `services/email_service.py` — Resend API, template email

Tutte le route usano `endpoint=` espliciti identici alla V2:
**i template non richiedono alcuna modifica**.

### db_utils.py
Layer di accesso al DB isolato. Interfaccia identica alla V2
(db_conn, db_execute, db_fetchone, db_fetchall, row_get).
Zero regression sulle query esistenti.

### SQLAlchemy + Alembic
- `extensions.py`: istanze condivise (db, csrf, limiter)
- `models.py`: modelli dichiarativi SQLAlchemy — specchio dello schema
- `alembic.ini` + `migrations/env.py`: Alembic configurato per autogenerare migrazioni future

Per generare la migrazione iniziale:
```bash
alembic revision --autogenerate -m "initial schema"
alembic upgrade head
```

### CSS in file statico
Il blocco `<style>` inline (~82 righe) è stato estratto in
`static/css/app.css`. `base.html` usa ora:
```html
<link rel="stylesheet" href="{{ url_for('static', filename='css/app.css') }}">
```

### Flask-Talisman (header sicurezza)
In produzione (RENDER o DATABASE_URL impostati):
- **HTTPS forzato** (redirect HTTP → HTTPS)
- **HSTS** con max-age 1 anno + includeSubDomains
- **CSP** calibrata:
  - `style-src 'self' fonts.googleapis.com fonts.gstatic.com`
  - `script-src 'self' 'unsafe-inline'` (rimuovere unsafe-inline quando gli script inline saranno in file .js separati)
  - `frame-ancestors 'none'` (equivalente X-Frame-Options: DENY)
  - `form-action 'self'`
- **X-Frame-Options: DENY** anche in sviluppo
- **X-Content-Type-Options: nosniff** anche in sviluppo

In locale: solo gli header minimali (no HTTPS forzato).

### Test automatici pytest
`tests/test_game_logic.py` — 18 test puri sulla logica di punteggio.
`tests/test_routes.py` — 15 test di integrazione su route protette, login, admin.

```bash
pip install pytest pytest-flask
pytest tests/ -v
```

## Azioni richieste post-deploy

1. `pip install -r requirements.txt` (nuove dipendenze)
2. Render fa auto-deploy → Talisman attivato automaticamente
3. Verifica che il link al CSS sia corretto visitando l'app
4. Opzionale: `alembic revision --autogenerate -m "initial"` per avere la migrazione baseline

## Rimandato (potenziale V4)
- Rimozione di `unsafe-inline` da script-src (richiede spostamento degli script inline di base.html)
- N+1 residue nelle route admin (carico giocatori per partita)
- Conversione query da text() a SQLAlchemy ORM
- CI/CD con pytest automatico su GitHub Actions
