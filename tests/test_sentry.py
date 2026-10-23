"""Sentry parte solo con SENTRY_DSN e senza dati personali."""

import sentry_sdk

import app as modulo_app


def test_senza_dsn_sentry_resta_spento(monkeypatch):
    monkeypatch.delenv('SENTRY_DSN', raising=False)
    chiamate = []
    monkeypatch.setattr(sentry_sdk, 'init', lambda **kw: chiamate.append(kw))
    assert modulo_app._inizializza_sentry() is False
    assert chiamate == []


def test_con_dsn_sentry_parte_senza_dati_personali(monkeypatch):
    monkeypatch.setenv('SENTRY_DSN', 'https://chiave@o0.ingest.sentry.io/0')
    monkeypatch.setenv('RENDER_GIT_COMMIT', 'abc123')
    chiamate = []
    monkeypatch.setattr(sentry_sdk, 'init', lambda **kw: chiamate.append(kw))
    assert modulo_app._inizializza_sentry() is True
    opzioni = chiamate[0]
    assert opzioni['send_default_pii'] is False
    assert opzioni['max_request_body_size'] == 'never'
    assert opzioni['release'] == 'abc123'
