"""Dietro il proxy di Render ogni utente deve avere il suo contatore di rate
limit: prima tutti condividevano l'IP del proxy e quindi un unico limite."""

import pytest

from extensions import limiter


@pytest.fixture(autouse=True)
def _reset_limiter():
    limiter.reset()
    yield
    limiter.reset()


def _tenta_login(client, ip):
    return client.post('/login', data={'nome_utente': 'nessuno', 'password': 'x'},
                       headers={'X-Forwarded-For': ip})


def test_utenti_con_ip_diversi_non_condividono_il_limite_del_login(client):
    for _ in range(5):
        assert _tenta_login(client, '203.0.113.10').status_code != 429
    assert _tenta_login(client, '203.0.113.10').status_code == 429

    assert _tenta_login(client, '198.51.100.20').status_code != 429
