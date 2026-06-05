"""
Test unitari per services/game_logic.py.

Questi test sono puri Python, non richiedono il DB né Flask.
Coprono la funzione calcola_punti_pronostico() in ogni caso possibile.
"""

import pytest
from services.game_logic import (
    calcola_punti_pronostico,
    PUNTI_ESITO, PUNTI_RISULTATO, PUNTI_MARCATORE, PUNTI_BONUS_TRIPLA,
)


# ─── Helper per costruire dict "row-compatibili" ──────────────────────────────

def partita(r_casa, r_osp, marcatore_reale=''):
    return {
        'risultato_casa_reale':   r_casa,
        'risultato_ospite_reale': r_osp,
        'marcatore_reale':        marcatore_reale,
    }


def pronostico(esito=None, r_casa=None, r_osp=None, marcatore=None):
    return {
        'esito_pronosticato':              esito,
        'risultato_casa_pronosticato':     r_casa,
        'risultato_ospite_pronosticato':   r_osp,
        'marcatore_pronosticato':          marcatore,
    }


# ─── Test: pronostico assente / partita senza risultato ───────────────────────

def test_pronostico_none_restituisce_zeri():
    p = partita(2, 1)
    out = calcola_punti_pronostico(None, p)
    assert out['totale'] == 0
    assert not any([out['esito_corretto'],
                    out['risultato_corretto'],
                    out['marcatore_corretto']])


def test_partita_senza_risultato_restituisce_zeri():
    p = {'risultato_casa_reale': None, 'risultato_ospite_reale': None,
         'marcatore_reale': ''}
    pron = pronostico(esito='1', r_casa=2, r_osp=0)
    out = calcola_punti_pronostico(pron, p)
    assert out['totale'] == 0


# ─── Test: esito 1/X/2 ────────────────────────────────────────────────────────

@pytest.mark.parametrize('r_casa,r_osp,esito_atteso', [
    (2, 1, '1'),
    (0, 0, 'X'),
    (1, 3, '2'),
])
def test_esito_corretto(r_casa, r_osp, esito_atteso):
    p    = partita(r_casa, r_osp)
    pron = pronostico(esito=esito_atteso)
    out  = calcola_punti_pronostico(pron, p)
    assert out['esito_corretto'] is True
    assert out['esito'] == PUNTI_ESITO


def test_esito_sbagliato():
    p    = partita(2, 1)   # esito reale = '1'
    pron = pronostico(esito='2')
    out  = calcola_punti_pronostico(pron, p)
    assert out['esito_corretto'] is False
    assert out['esito'] == 0


# ─── Test: risultato esatto ───────────────────────────────────────────────────

def test_risultato_esatto_corretto():
    p    = partita(2, 1)
    pron = pronostico(r_casa=2, r_osp=1)
    out  = calcola_punti_pronostico(pron, p)
    assert out['risultato_corretto'] is True
    assert out['risultato'] == PUNTI_RISULTATO


def test_risultato_esatto_errato():
    p    = partita(2, 1)
    pron = pronostico(r_casa=3, r_osp=1)
    out  = calcola_punti_pronostico(pron, p)
    assert out['risultato_corretto'] is False
    assert out['risultato'] == 0


def test_risultato_parzialmente_corretto_non_da_punti():
    """Indovinare solo un gol non basta."""
    p    = partita(2, 1)
    pron = pronostico(r_casa=2, r_osp=0)
    out  = calcola_punti_pronostico(pron, p)
    assert out['risultato_corretto'] is False


# ─── Test: marcatore ─────────────────────────────────────────────────────────

def test_marcatore_corretto():
    p    = partita(1, 0, marcatore_reale='Lautaro Martinez')
    pron = pronostico(marcatore='Lautaro Martinez')
    out  = calcola_punti_pronostico(pron, p)
    assert out['marcatore_corretto'] is True
    assert out['marcatore'] == PUNTI_MARCATORE


def test_marcatore_case_insensitive():
    p    = partita(1, 0, marcatore_reale='Lautaro Martinez')
    pron = pronostico(marcatore='lautaro martinez')
    out  = calcola_punti_pronostico(pron, p)
    assert out['marcatore_corretto'] is True


def test_marcatore_multiplo_uno_indovinato():
    p    = partita(2, 0, marcatore_reale='Lukaku, Lautaro Martinez')
    pron = pronostico(marcatore='Lukaku')
    out  = calcola_punti_pronostico(pron, p)
    assert out['marcatore_corretto'] is True


def test_marcatore_sbagliato():
    p    = partita(1, 0, marcatore_reale='Lautaro Martinez')
    pron = pronostico(marcatore='Vlahovic')
    out  = calcola_punti_pronostico(pron, p)
    assert out['marcatore_corretto'] is False
    assert out['marcatore'] == 0


def test_nessun_marcatore_in_partita_0_0():
    p    = partita(0, 0)
    pron = pronostico(marcatore='Nessun marcatore')
    out  = calcola_punti_pronostico(pron, p)
    assert out['marcatore_corretto'] is True


def test_nessun_marcatore_in_partita_con_gol():
    """'Nessun marcatore' è sbagliato se ci sono stati gol."""
    p    = partita(1, 0, 'Lukaku')
    pron = pronostico(marcatore='Nessun marcatore')
    out  = calcola_punti_pronostico(pron, p)
    assert out['marcatore_corretto'] is False


# ─── Test: bonus tripla ───────────────────────────────────────────────────────

def test_bonus_tripla_con_tutto_corretto():
    p    = partita(2, 0, 'Lautaro Martinez')
    pron = pronostico(esito='1', r_casa=2, r_osp=0,
                      marcatore='Lautaro Martinez')
    out  = calcola_punti_pronostico(pron, p)
    assert out['esito_corretto']      is True
    assert out['risultato_corretto']  is True
    assert out['marcatore_corretto']  is True
    assert out['bonus'] == PUNTI_BONUS_TRIPLA
    assert out['totale'] == (PUNTI_ESITO + PUNTI_RISULTATO
                             + PUNTI_MARCATORE + PUNTI_BONUS_TRIPLA)


def test_bonus_tripla_assente_senza_marcatore():
    p    = partita(2, 0, 'Lautaro Martinez')
    pron = pronostico(esito='1', r_casa=2, r_osp=0,
                      marcatore='Vlahovic')  # marcatore sbagliato
    out  = calcola_punti_pronostico(pron, p)
    assert out['bonus'] == 0


# ─── Test: totale complessivo ─────────────────────────────────────────────────

def test_totale_solo_esito():
    p    = partita(2, 1)
    pron = pronostico(esito='1')
    out  = calcola_punti_pronostico(pron, p)
    assert out['totale'] == PUNTI_ESITO


def test_totale_esito_più_risultato():
    p    = partita(2, 1)
    pron = pronostico(esito='1', r_casa=2, r_osp=1)
    out  = calcola_punti_pronostico(pron, p)
    assert out['totale'] == PUNTI_ESITO + PUNTI_RISULTATO


def test_tutto_sbagliato_totale_zero():
    p    = partita(1, 0, 'Lukaku')
    pron = pronostico(esito='2', r_casa=0, r_osp=1,
                      marcatore='Vlahovic')
    out  = calcola_punti_pronostico(pron, p)
    assert out['totale'] == 0
