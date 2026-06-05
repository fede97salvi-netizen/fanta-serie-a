# Script di utility

Questi script sono utilità di manutenzione **una tantum** o **occasionali**.
Non vengono caricati dall'app principale e sono pensati per girare in locale
su `database.db` (SQLite).

## Configurazione

Prima di lanciarli, imposta la chiave API della Football-Data:

```bash
export FOOTBALL_API_KEY="la_tua_chiave"
```

Su Windows PowerShell:

```powershell
$env:FOOTBALL_API_KEY="la_tua_chiave"
```

## Elenco

| Script                  | Cosa fa                                                            |
|-------------------------|--------------------------------------------------------------------|
| `importa_calendario.py` | Scarica il calendario Serie A da football-data.org                 |
| `importa_da_api.py`     | Importa partite + risultati da API (variante con dettagli)         |
| `importa_giocatori.py`  | Importa i giocatori da un CSV di quotazioni Fantacalcio            |
| `pulisci_vecchie.py`    | Cancella partite vecchie (stagioni precedenti)                     |
| `fix_pronostici.py`     | Ripara pronostici "orfani" rimasti senza partita di riferimento    |
| `fix_niko.py`           | Una tantum: trasferimento dati tra due ID utente                   |

## Avvio

Lancia gli script **dalla cartella radice del progetto** (non da `scripts/`),
in modo che `database.db` venga trovato correttamente:

```bash
python scripts/importa_calendario.py
```
