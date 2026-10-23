"""Nomi brevi delle squadre nelle card (solo visualizzazione)."""

import pytest

from services.game_logic import nome_squadra_breve


@pytest.mark.parametrize('ufficiale, breve', [
    ('FC INTERNAZIONALE MILANO', 'INTER'),
    ('ACF FIORENTINA', 'FIORENTINA'),
    ('TORINO FC', 'TORINO'),
    ('AC MONZA', 'MONZA'),
    ('GENOA CFC', 'GENOA'),
    ('VENEZIA FC', 'VENEZIA'),
    ('AC MILAN', 'MILAN'),
    ('JUVENTUS FC', 'JUVENTUS'),
    ('SSC NAPOLI', 'NAPOLI'),
    ('AS ROMA', 'ROMA'),
    ('SS LAZIO', 'LAZIO'),
    ('ATALANTA BC', 'ATALANTA'),
    ('BOLOGNA FC 1909', 'BOLOGNA'),
    ('PARMA CALCIO 1913', 'PARMA'),
    ('COMO 1907', 'COMO'),
    ('US SASSUOLO CALCIO', 'SASSUOLO'),
    ('HELLAS VERONA FC', 'VERONA'),
    ('INTER', 'INTER'),
    ('', ''),
])
def test_nome_breve(ufficiale, breve):
    assert nome_squadra_breve(ufficiale) == breve
