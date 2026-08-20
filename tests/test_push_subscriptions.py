"""Test per salva_subscription_push: niente righe duplicate per lo stesso
dispositivo quando la stessa subscription viene salvata prima e dopo il login
(altrimenti lo stesso dispositivo riceve ogni notifica due volte)."""

from tests.conftest import _crea_utente
from db_utils import db_conn, db_fetchall, row_get


def test_stessa_subscription_prima_e_dopo_login_non_duplica(app):
    from invia_notifiche import salva_subscription_push

    uid = _crea_utente('utente_push_dedup')
    sub = {
        'endpoint': 'https://push.test/stesso-dispositivo',
        'keys': {'auth': 'a', 'p256dh': 'p'},
    }

    # 1) iscrizione fatta da anonimo (nessuna sessione loggata)
    salva_subscription_push(None, sub)

    # 2) stessa identica subscription, questa volta da loggato
    salva_subscription_push('utente_push_dedup', sub)

    with db_conn() as conn:
        righe = db_fetchall(
            conn,
            "SELECT id_utente FROM push_subscriptions WHERE subscription_info LIKE ?",
            ('%stesso-dispositivo%',),
        )
    assert len(righe) == 1
    assert row_get(righe[0], 'id_utente') == uid


def test_subscription_diverse_restano_righe_separate(app):
    from invia_notifiche import salva_subscription_push

    uid_a = _crea_utente('utente_push_a')
    uid_b = _crea_utente('utente_push_b')

    salva_subscription_push('utente_push_a', {
        'endpoint': 'https://push.test/dispositivo-a', 'keys': {'auth': 'a', 'p256dh': 'p'},
    })
    salva_subscription_push('utente_push_b', {
        'endpoint': 'https://push.test/dispositivo-b', 'keys': {'auth': 'a', 'p256dh': 'p'},
    })

    with db_conn() as conn:
        riga_a = db_fetchall(conn, "SELECT id_utente FROM push_subscriptions WHERE subscription_info LIKE ?",
                             ('%dispositivo-a%',))
        riga_b = db_fetchall(conn, "SELECT id_utente FROM push_subscriptions WHERE subscription_info LIKE ?",
                             ('%dispositivo-b%',))
    assert len(riga_a) == 1 and row_get(riga_a[0], 'id_utente') == uid_a
    assert len(riga_b) == 1 and row_get(riga_b[0], 'id_utente') == uid_b
