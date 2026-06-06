-- ============================================================
-- Migrazione schema Fanta Mondiali 2026
-- Da eseguire su Supabase SQL Editor prima del deploy
-- ============================================================

-- 1. pronostici_eliminazione: aggiunge campi unificati (stesso sistema gironi)
ALTER TABLE pronostici_eliminazione
  ADD COLUMN IF NOT EXISTS esito_pronosticato TEXT,
  ADD COLUMN IF NOT EXISTS risultato_casa_pronosticato INTEGER,
  ADD COLUMN IF NOT EXISTS risultato_ospite_pronosticato INTEGER,
  ADD COLUMN IF NOT EXISTS marcatore_pronosticato TEXT;

-- 2. pronostici_torneo: semplificato → solo vincitore + capocannoniere
ALTER TABLE pronostici_torneo
  DROP COLUMN IF EXISTS finalista,
  DROP COLUMN IF EXISTS semifinalista_1,
  DROP COLUMN IF EXISTS semifinalista_2;

-- 3. partite: colonna reminder_inviato per invio automatico email
ALTER TABLE partite
  ADD COLUMN IF NOT EXISTS reminder_inviato BOOLEAN NOT NULL DEFAULT FALSE;

-- 4. Verifica finale
SELECT table_name, column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name IN ('pronostici_eliminazione','pronostici_torneo','partite')
ORDER BY table_name, ordinal_position;
