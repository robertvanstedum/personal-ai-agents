-- Migration 010: Portuguese vocabulary soft-hide.
-- status 'oculto' keeps the row and drops it from Palavras, drills, and Anki export.
-- Apply with the same psql step as 007 before the Ocultar button can succeed.
-- The word list already ignores this status, so the page still loads before this runs.

ALTER TABLE portuguese.vocabulary DROP CONSTRAINT IF EXISTS vocabulary_status_check;
ALTER TABLE portuguese.vocabulary ADD CONSTRAINT vocabulary_status_check
  CHECK (status IN (
    'biblioteca',
    'praticando',
    'pronto_para_testar',
    'aprendido',
    'oculto'
  ));
