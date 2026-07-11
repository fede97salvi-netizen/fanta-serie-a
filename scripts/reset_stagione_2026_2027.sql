-- =====================================================================
-- FantaSerieA — Reset stagione per la nuova stagione 2026/2027
-- =====================================================================
-- Cosa fa:
--   * MANTIENE gli account utente (tabella "utenti") e le loro email
--   * AZZERA tutti i dati di gioco: partite, pronostici (giornata e
--     iniziali), punteggi, punteggi di giornata, stato giornate
--   * SVUOTA le rose giocatori (le reimporterai per la nuova stagione)
--   * Sblocca i pronostici iniziali e ripulisce i risultati finali
--   * Riporta la classifica a 0 per tutti gli utenti
--
-- Target: PostgreSQL (Supabase).
--
-- PRIMA DI ESEGUIRE — fai un backup:
--   pg_dump "$DATABASE_URL" > backup_pre_reset_$(date +%F).sql
--
-- Esecuzione (psql):
--   psql "$DATABASE_URL" -f reset_stagione_2026_2027.sql
-- =====================================================================

BEGIN;

-- 1) Dati di gioco legati agli utenti/partite
DELETE FROM pronostici_giornata;
DELETE FROM pronostici_iniziali;
DELETE FROM punteggi_giornata;
DELETE FROM punteggi;

-- 2) Partite e stato giornate
DELETE FROM partite;
DELETE FROM stato_giornata;

-- 3) Rose giocatori (verranno reimportate)
DELETE FROM giocatori;

-- 4) Sblocca i pronostici iniziali per la nuova stagione
INSERT INTO stato_pronostici_iniziali (id, is_locked)
VALUES (1, FALSE)
ON CONFLICT (id) DO UPDATE SET is_locked = FALSE;

-- 5) Ripulisci i risultati finali di stagione
INSERT INTO risultati_finali (id) VALUES (1)
ON CONFLICT (id) DO NOTHING;
UPDATE risultati_finali
   SET squadra_1 = NULL, squadra_2 = NULL, squadra_3 = NULL,
       squadra_4 = NULL, capocannoniere = NULL
 WHERE id = 1;

-- 6) Classifica a zero per tutti gli utenti mantenuti
INSERT INTO punteggi (id_utente, punteggio_totale)
SELECT id, 0 FROM utenti;

COMMIT;

-- =====================================================================
-- Verifica (esegui dopo il commit; devono essere tutti 0 tranne "utenti")
-- =====================================================================
-- SELECT 'utenti' AS tabella, COUNT(*) FROM utenti
-- UNION ALL SELECT 'partite', COUNT(*) FROM partite
-- UNION ALL SELECT 'pronostici_giornata', COUNT(*) FROM pronostici_giornata
-- UNION ALL SELECT 'pronostici_iniziali', COUNT(*) FROM pronostici_iniziali
-- UNION ALL SELECT 'punteggi_giornata', COUNT(*) FROM punteggi_giornata
-- UNION ALL SELECT 'giocatori', COUNT(*) FROM giocatori
-- UNION ALL SELECT 'stato_giornata', COUNT(*) FROM stato_giornata;
