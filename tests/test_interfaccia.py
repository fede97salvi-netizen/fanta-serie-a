"""
Test dell'interfaccia (restyling grafico, settembre 2026).

Verificano che il nuovo aspetto grafico non abbia rotto nulla:
  - tutte le pagine si aprono (utente, visitatore, admin) senza errori;
  - i form contengono ancora gli stessi campi che il server si aspetta,
    e inviandoli "come farebbe il browser" i dati vengono salvati;
  - gli agganci usati dagli script (id, onclick) sono ancora presenti;
  - nessun template usa blocchi <style> (bloccati dalla CSP in produzione);
  - CSS e font self-hosted esistono e vengono serviti;
  - ogni classe CSS usata nelle pagine utente è definita nel foglio di stile.
"""

import os
import re
from html.parser import HTMLParser

import pytest

from tests.conftest import _crea_utente
from db_utils import db_conn, db_execute, db_fetchone, db_fetchall, db_commit, row_get

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL_DIR = os.path.join(BASE_DIR, 'templates')
CSS_PATH = os.path.join(BASE_DIR, 'static', 'css', 'app.css')

G_APERTA = 61      # giornata con partite future (pronosticabili)
G_ARCHIVIO = 62    # giornata archiviata con risultati


# ─── Parser minimale: ricostruisce i form come li invierebbe un browser ─────

class _FormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []
        self._form = None
        self._select = None
        self.ids = set()
        self.onclicks = []
        self.style_tags = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if 'id' in a:
            self.ids.add(a['id'])
        if 'onclick' in a:
            self.onclicks.append(a['onclick'])
        if tag == 'style':
            self.style_tags += 1
        if tag == 'form':
            self._form = {'action': a.get('action'), 'method': (a.get('method') or 'get').lower(),
                          'fields': [], 'submit': False}
            self.forms.append(self._form)
        if self._form is None:
            return
        if tag == 'input':
            t = (a.get('type') or 'text').lower()
            if t == 'submit':
                self._form['submit'] = True
            self._form['fields'].append({'tag': 'input', 'type': t, 'name': a.get('name'),
                                         'value': a.get('value', ''), 'checked': 'checked' in a})
        elif tag == 'button' and (a.get('type') or 'submit').lower() == 'submit':
            self._form['submit'] = True
        elif tag == 'select':
            self._select = {'tag': 'select', 'name': a.get('name'), 'options': [], 'selected': None}
            self._form['fields'].append(self._select)
        elif tag == 'option' and self._select is not None:
            self._select['options'].append(a.get('value', ''))
            if 'selected' in a:
                self._select['selected'] = a.get('value', '')

    def handle_endtag(self, tag):
        if tag == 'form':
            self._form = None
        if tag == 'select':
            self._select = None


def _parse(html):
    p = _FormParser()
    p.feed(html)
    return p


@pytest.fixture(autouse=True)
def _azzera_rate_limit():
    """I POST di login/registrazione hanno un rate limit: lo azzeriamo per non
    influenzare gli altri file di test che girano nella stessa sessione."""
    from extensions import limiter
    limiter.reset()
    yield
    limiter.reset()


def _login(client, nome, admin=False):
    with client.session_transaction() as s:
        s['nome_utente'] = nome
        s['is_admin'] = admin


@pytest.fixture(scope='module')
def dati():
    """Popola il DB di test con un piccolo campionato realistico."""
    uid = _crea_utente('ui_mario')
    _crea_utente('ui_lucia')
    _crea_utente('ui_admin', is_admin=True)
    with db_conn() as conn:
        for g, arch in ((G_APERTA, 0), (G_ARCHIVIO, 1)):
            db_execute(conn, 'INSERT OR IGNORE INTO stato_giornata (giornata, is_attiva, is_in_archivio) '
                             'VALUES (?, 0, ?)', (g, arch))
        partite = [('Inter', 'Juventus'), ('Milan', 'Napoli'), ('Roma', 'Lazio')]
        for casa, osp in partite:
            db_execute(conn, 'INSERT INTO partite (giornata, squadra_casa, squadra_ospite, pronosticabile, '
                             'data_ora_partita) VALUES (?, ?, ?, 1, ?)',
                       (G_APERTA, casa, osp, '2099-05-10 18:00:00'))
            db_execute(conn, 'INSERT INTO partite (giornata, squadra_casa, squadra_ospite, pronosticabile, '
                             'data_ora_partita, risultato_casa_reale, risultato_ospite_reale, marcatore_reale) '
                             'VALUES (?, ?, ?, 1, ?, 2, 1, ?)',
                       (G_ARCHIVIO, osp, casa, '2020-05-10 18:00:00', 'Lautaro Martinez'))
        db_execute(conn, 'INSERT INTO partite (giornata, squadra_casa, squadra_ospite, pronosticabile, '
                         'data_ora_partita, risultato_casa_reale, risultato_ospite_reale) '
                         'VALUES (?, "Genoa", "Como", 0, "2020-05-10 15:00:00", 0, 0)', (G_ARCHIVIO,))
        for nome, sq in (('Lautaro Martinez', 'Inter'), ('Vlahovic', 'Juventus'), ('Leao', 'Milan')):
            db_execute(conn, 'INSERT INTO giocatori (nome_giocatore, squadra) VALUES (?, ?)', (nome, sq))
        arch = db_fetchall(conn, 'SELECT id FROM partite WHERE giornata = ?', (G_ARCHIVIO,))
        for p in arch:
            db_execute(conn, 'INSERT INTO pronostici_giornata (id_utente, id_partita, esito_pronosticato, '
                             'risultato_casa_pronosticato, risultato_ospite_pronosticato, marcatore_pronosticato) '
                             'VALUES (?, ?, "1", 2, 1, "Lautaro Martinez")', (uid, row_get(p, 'id')))
        db_commit(conn)
        aperte = db_fetchall(conn, 'SELECT id FROM partite WHERE giornata = ? ORDER BY id', (G_APERTA,))
    return {'uid': uid, 'pid_aperte': [row_get(p, 'id') for p in aperte]}


# ─── 1. Tutte le pagine si aprono ───────────────────────────────────────────

PAGINE_VISITATORE = ['/', '/login', '/registrazione', '/recupera-password']
PAGINE_UTENTE = ['/', '/classifica', '/giornate', f'/giornata/{G_ARCHIVIO}',
                 f'/giornata/{G_ARCHIVIO}/classifica-cumulativa', f'/pronostici-giornata/{G_APERTA}',
                 f'/pronostici-giornata/{G_ARCHIVIO}', '/profilo', '/pronostici-iniziali', '/cambia-password']
PAGINE_ADMIN = ['/admin', '/admin/utenti', '/admin/pagamenti', '/admin/gestisci-partite',
                f'/admin/gestisci-pronostici/{G_ARCHIVIO}', '/admin/gestisci-pronostici-iniziali',
                '/admin/gestisci-finalizzazione', f'/admin/modifica-giornata-archiviata/{G_ARCHIVIO}']


@pytest.mark.parametrize('url', PAGINE_VISITATORE)
def test_pagine_visitatore_200(client, url):
    r = client.get(url)
    assert r.status_code == 200, url
    html = r.data.decode('utf-8')
    assert 'css/app.css' in html
    assert 'fsa-tab-bar' not in html      # la tab bar è solo per utenti loggati


@pytest.mark.parametrize('url', PAGINE_UTENTE)
def test_pagine_utente_200(client, dati, url):
    _login(client, 'ui_mario')
    r = client.get(url)
    assert r.status_code == 200, url
    html = r.data.decode('utf-8')
    assert 'fsa-tab-bar' in html
    assert 'profileDrawer' in html


@pytest.mark.parametrize('url', PAGINE_ADMIN)
def test_pagine_admin_200(client, dati, url):
    _login(client, 'ui_admin', admin=True)
    r = client.get(url)
    assert r.status_code == 200, url


def test_pronostici_iniziali_bloccati_200(client, dati):
    _login(client, 'ui_mario')
    with db_conn() as conn:
        db_execute(conn, 'UPDATE stato_pronostici_iniziali SET is_locked = 1 WHERE id = 1')
        db_commit(conn)
    try:
        r = client.get('/pronostici-iniziali')
        assert r.status_code == 200
        assert 'Le scommesse sono chiuse' in r.data.decode('utf-8')
    finally:
        with db_conn() as conn:
            db_execute(conn, 'UPDATE stato_pronostici_iniziali SET is_locked = 0 WHERE id = 1')
            db_commit(conn)


def test_pronostici_iniziali_bloccati_tabella_e_schede(client, dati):
    """Scommesse chiuse: stessi dati sia nella tabella (PC) sia nelle schede (telefono)."""
    _login(client, 'ui_mario')
    with db_conn() as conn:
        db_execute(conn, 'INSERT INTO pronostici_iniziali (id_utente, squadra_1, squadra_2, squadra_3, squadra_4, '
                         'capocannoniere) VALUES (?, "FIORENTINA", "JUVENTUS", "ATALANTA", "BOLOGNA", "Mateo Retegui")',
                   (dati['uid'],))
        db_execute(conn, 'UPDATE stato_pronostici_iniziali SET is_locked = 1 WHERE id = 1')
        db_commit(conn)
    try:
        html = client.get('/pronostici-iniziali').data.decode('utf-8')
        assert 'pi-table' in html and 'pi-cards' in html
        assert 'table-layout:fixed' not in html
        tabella = html.split('pi-cards')[0]
        schede = html.split('class="pi-cards"')[1]
        for v in ('FIORENTINA', 'JUVENTUS', 'ATALANTA', 'BOLOGNA', 'Mateo Retegui', 'ui_mario'):
            assert v in tabella and v in schede, v
        assert 'pi-card is-me' in schede
    finally:
        with db_conn() as conn:
            db_execute(conn, 'UPDATE stato_pronostici_iniziali SET is_locked = 0 WHERE id = 1')
            db_execute(conn, 'DELETE FROM pronostici_iniziali WHERE id_utente = ?', (dati['uid'],))
            db_commit(conn)


def test_pagine_errore_personalizzate(client):
    r = client.get('/pagina-che-non-esiste')
    assert r.status_code == 404
    assert 'Pagina non trovata' in r.data.decode('utf-8')


# ─── 2. Form pronostici: stessi campi e salvataggio reale ───────────────────

def test_form_pronostici_contiene_i_campi_attesi(client, dati):
    _login(client, 'ui_mario')
    html = client.get(f'/pronostici-giornata/{G_APERTA}').data.decode('utf-8')
    p = _parse(html)
    form = [f for f in p.forms if f['method'] == 'post'][0]
    nomi = {f['name'] for f in form['fields'] if f.get('name')}
    assert 'csrf_token' in nomi
    assert form['submit'], 'il bottone di salvataggio deve stare dentro il form'
    for pid in dati['pid_aperte']:
        assert {f'esito_{pid}', f'risultato_casa_{pid}', f'risultato_ospite_{pid}', f'marcatore_{pid}'} <= nomi
        radio = [f['value'] for f in form['fields'] if f.get('name') == f'esito_{pid}']
        assert radio == ['1', 'X', '2']
        sel = [f for f in form['fields'] if f.get('name') == f'marcatore_{pid}'][0]
        assert 'Nessun Marcatore' in sel['options'] and 'Autogol' in sel['options']
    # Il radio resta nel DOM (nascosto solo visivamente) e quindi viene inviato
    assert 'display:none' not in html.split('esito-radio')[0][-300:]


def test_salvataggio_pronostici_come_da_browser(client, dati):
    """Compila il form estratto dalla pagina e lo invia: i dati devono finire nel DB."""
    _login(client, 'ui_mario')
    html = client.get(f'/pronostici-giornata/{G_APERTA}').data.decode('utf-8')
    form = [f for f in _parse(html).forms if f['method'] == 'post'][0]
    pid1, pid2 = dati['pid_aperte'][:2]
    scelte = {f'esito_{pid1}': 'X', f'risultato_casa_{pid1}': '1', f'risultato_ospite_{pid1}': '1',
              f'marcatore_{pid1}': 'Lautaro Martinez', f'esito_{pid2}': '2'}
    data = {}
    for f in form['fields']:
        n = f.get('name')
        if not n:
            continue
        if f['tag'] == 'select':
            data[n] = scelte.get(n, f['selected'] or '')
        elif f['type'] == 'radio':
            if n in scelte:
                data[n] = scelte[n]
            elif f['checked']:
                data[n] = f['value']
        else:
            data[n] = scelte.get(n, f['value'])
    r = client.post(f'/pronostici-giornata/{G_APERTA}', data=data)
    assert r.status_code == 302
    with db_conn() as conn:
        p1 = db_fetchone(conn, 'SELECT * FROM pronostici_giornata WHERE id_utente=? AND id_partita=?',
                         (dati['uid'], pid1))
        p2 = db_fetchone(conn, 'SELECT * FROM pronostici_giornata WHERE id_utente=? AND id_partita=?',
                         (dati['uid'], pid2))
    assert row_get(p1, 'esito_pronosticato') == 'X'
    assert row_get(p1, 'risultato_casa_pronosticato') == 1
    assert row_get(p1, 'risultato_ospite_pronosticato') == 1
    assert row_get(p1, 'marcatore_pronosticato') == 'Lautaro Martinez'
    assert row_get(p2, 'esito_pronosticato') == '2'

    # Riaprendo la pagina le scelte salvate risultano selezionate
    html = client.get(f'/pronostici-giornata/{G_APERTA}').data.decode('utf-8')
    form = [f for f in _parse(html).forms if f['method'] == 'post'][0]
    checked = {f['name']: f['value'] for f in form['fields'] if f.get('type') == 'radio' and f['checked']}
    assert checked[f'esito_{pid1}'] == 'X' and checked[f'esito_{pid2}'] == '2'
    sel = [f for f in form['fields'] if f.get('name') == f'marcatore_{pid1}'][0]
    assert sel['selected'] == 'Lautaro Martinez'
    assert 'badge-saved' in html


def test_partita_scaduta_mostra_riepilogo_senza_campi(client, dati):
    _login(client, 'ui_mario')
    html = client.get(f'/pronostici-giornata/{G_ARCHIVIO}').data.decode('utf-8')
    assert 'badge-locked' in html and 'Il tuo pronostico' in html
    assert 'name="esito_' not in html


# ─── 3. Altri form: stessi campi di prima ───────────────────────────────────

@pytest.mark.parametrize('url,attesi,loggato', [
    ('/login', {'csrf_token', 'nome_utente', 'password', 'remember'}, False),
    ('/registrazione', {'csrf_token', 'nome_utente', 'password'}, False),
    ('/recupera-password', {'csrf_token', 'nome_utente'}, False),
    ('/cambia-password', {'csrf_token', 'nuova_password', 'conferma_password'}, True),
    ('/pronostici-iniziali', {'csrf_token', 'squadra_1', 'squadra_2', 'squadra_3', 'squadra_4',
                              'capocannoniere'}, True),
])
def test_campi_form(client, dati, url, attesi, loggato):
    if loggato:
        _login(client, 'ui_mario')
    p = _parse(client.get(url).data.decode('utf-8'))
    form = [f for f in p.forms if f['method'] == 'post'][0]
    nomi = {f['name'] for f in form['fields'] if f.get('name')}
    assert attesi <= nomi
    assert form['submit']


def test_profilo_due_form_email_e_password(client, dati):
    _login(client, 'ui_mario')
    p = _parse(client.get('/profilo').data.decode('utf-8'))
    posts = [f for f in p.forms if f['method'] == 'post']
    azioni = [next(x['value'] for x in f['fields'] if x.get('name') == 'azione') for f in posts]
    assert azioni == ['email', 'password']


def test_login_reale_dal_form(client):
    _crea_utente('ui_login', 'segreta123')
    r = client.post('/login', data={'nome_utente': 'ui_login', 'password': 'segreta123', 'remember': 'on'})
    assert r.status_code == 302
    r = client.get('/')
    assert 'Bentornato' in r.data.decode('utf-8')


def _invia_form_profilo(client, azione, valori):
    """Invia il form del profilo usando i nomi dei campi presenti nella pagina."""
    p = _parse(client.get('/profilo').data.decode('utf-8'))
    form = [f for f in p.forms if f['method'] == 'post'
            and any(x.get('name') == 'azione' and x['value'] == azione for x in f['fields'])][0]
    data = {}
    for f in form['fields']:
        n = f.get('name')
        if not n:
            continue
        data[n] = valori.get(f.get('type'), valori.get(n, f.get('value', '')))
    return client.post('/profilo', data=data, follow_redirects=True)


def test_salvataggio_email_profilo(client, dati):
    """Bug corretto: il campo del form non corrispondeva a quello letto dal server,
    e l'email veniva sovrascritta con una stringa vuota."""
    _login(client, 'ui_lucia')
    r = _invia_form_profilo(client, 'email', {'email': 'lucia@example.com'})
    with db_conn() as conn:
        row = db_fetchone(conn, 'SELECT email FROM utenti WHERE nome_utente = ?', ('ui_lucia',))
    assert row_get(row, 'email') == 'lucia@example.com'
    html = r.data.decode('utf-8')
    assert 'lucia@example.com' in html and 'Email aggiornata' in html


def test_email_non_valida_mostra_errore_e_non_salva(client, dati):
    _login(client, 'ui_lucia')
    _invia_form_profilo(client, 'email', {'email': 'lucia@example.com'})
    r = _invia_form_profilo(client, 'email', {'email': 'non-una-email'})
    assert 'Formato email non valido' in r.data.decode('utf-8')
    with db_conn() as conn:
        row = db_fetchone(conn, 'SELECT email FROM utenti WHERE nome_utente = ?', ('ui_lucia',))
    assert row_get(row, 'email') == 'lucia@example.com'


def test_password_diverse_mostrano_errore(client, dati):
    _login(client, 'ui_lucia')
    r = _invia_form_profilo(client, 'password', {'nuova_password': 'abcdef12', 'conferma_password': 'xyz98765'})
    assert 'Le password non coincidono' in r.data.decode('utf-8')


# ─── 4. Agganci JavaScript ancora presenti ──────────────────────────────────

def test_agganci_js_base(client, dati):
    _login(client, 'ui_mario')
    html = client.get('/').data.decode('utf-8')
    p = _parse(html)
    assert {'profileBtn', 'profileOverlay', 'profileDrawer', 'drawerEmail'} <= p.ids
    assert 'function toggleDrawer' in html and 'function closeDrawer' in html
    assert 'attivaNotifiche()' in ' '.join(p.onclicks)
    assert "fetch('/api/profilo-info')" in html


def test_agganci_js_giornata(client, dati):
    _login(client, 'ui_mario')
    html = client.get(f'/giornata/{G_ARCHIVIO}').data.decode('utf-8')
    p = _parse(html)
    assert {'sezione-classifica', 'arrow-classifica', 'sezione-pronostici', 'arrow-pronostici'} <= p.ids
    assert "toggleSezione('classifica')" in p.onclicks and "toggleSezione('pronostici')" in p.onclicks
    # Lo script confronta style.display === 'none': lo stato iniziale deve restare inline
    assert re.search(r'id="sezione-classifica"[^>]*style="display:none;"', html)


def test_tab_attiva_evidenziata(client, dati):
    _login(client, 'ui_mario')
    html = client.get('/classifica').data.decode('utf-8')
    assert re.search(r'href="/classifica" class="fsa-tab active"', html)


def test_evidenziazione_utente_in_classifica(client, dati):
    _login(client, 'ui_mario')
    html = client.get('/classifica').data.decode('utf-8')
    assert re.search(r'rank-row[^"]*is-me', html)
    assert 'me-tag' in html


# ─── 5. Vincoli tecnici: CSP, file statici, classi CSS ──────────────────────

def _templates():
    return [f for f in os.listdir(TPL_DIR) if f.endswith('.html')]


def test_nessun_blocco_style_nei_template():
    """La CSP di produzione blocca gli <style> inline: tutto deve stare in app.css."""
    for nome in _templates():
        testo = open(os.path.join(TPL_DIR, nome), encoding='utf-8').read()
        assert '<style' not in testo.lower(), nome


def test_nessun_font_esterno():
    base = open(os.path.join(TPL_DIR, 'base.html'), encoding='utf-8').read()
    assert 'fonts.googleapis.com' not in base


def test_file_statici_referenziati_esistono(client):
    """Ogni url_for('static', ...) nei template e ogni url(...) nel CSS punta a un file reale."""
    rif = set()
    for nome in _templates():
        testo = open(os.path.join(TPL_DIR, nome), encoding='utf-8').read()
        rif |= set(re.findall(r"url_for\('static', filename='([^']+)'\)", testo))
    css = open(CSS_PATH, encoding='utf-8').read()
    for u in re.findall(r"url\('\.\./([^']+)'\)", css):
        rif.add(u)
    assert rif, 'nessun riferimento trovato'
    for f in rif:
        assert os.path.isfile(os.path.join(BASE_DIR, 'static', f)), f
        r = client.get('/static/' + f)
        assert r.status_code == 200, f
        r.close()


def test_classi_usate_sono_definite_nel_css():
    """Ogni classe usata nelle pagine utente deve esistere in app.css (niente stili 'persi')."""
    css = open(CSS_PATH, encoding='utf-8').read()
    definite = set(re.findall(r'\.([a-zA-Z][\w-]*)', css))
    ignorate = {'team-home'}   # classi usate solo come aggancio semantico
    mancanti = {}
    for nome in _templates():
        if nome.startswith('admin'):
            continue
        testo = open(os.path.join(TPL_DIR, nome), encoding='utf-8').read()
        testo = re.sub(r'{%.*?%}|{{.*?}}', ' ', testo, flags=re.S)
        for attr in re.findall(r'class="([^"]*)"', testo):
            for c in attr.split():
                if c.endswith('-'):
                    continue   # frammento di classe composta da Jinja (es. alert-{{...}})
                if c not in definite and c not in ignorate and c not in ('selected', 'active'):
                    mancanti.setdefault(nome, set()).add(c)
    assert not mancanti, mancanti


def test_css_bilanciato_e_con_effetti_luminosi():
    css = open(CSS_PATH, encoding='utf-8').read()
    senza_commenti = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    assert senza_commenti.count('{') == senza_commenti.count('}'), 'parentesi graffe sbilanciate'
    # Effetto "bottone che si illumina" su hover e pressione per i bottoni principali
    for cls in ('btn-primary', 'btn-success', 'btn-secondary'):
        assert re.search(rf'\.{cls}:active\s*{{[^}}]*box-shadow', senza_commenti), cls
        assert re.search(rf'\.{cls}:hover\s*{{[^}}]*box-shadow', senza_commenti), cls
    assert 'prefers-reduced-motion' in css
