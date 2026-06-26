-- Migration 007: Portuguese vocabulary — four status states (Portuguese values)
-- Renames English status values to Portuguese and adds pronto_para_testar

-- Rename any existing English values to Portuguese
UPDATE portuguese.vocabulary SET status = 'biblioteca'  WHERE status = 'library';
UPDATE portuguese.vocabulary SET status = 'praticando'  WHERE status = 'practice';
UPDATE portuguese.vocabulary SET status = 'aprendido'   WHERE status = 'mastered';

-- Update column default
ALTER TABLE portuguese.vocabulary ALTER COLUMN status SET DEFAULT 'biblioteca';

-- Add four-state check constraint
ALTER TABLE portuguese.vocabulary DROP CONSTRAINT IF EXISTS vocabulary_status_check;
ALTER TABLE portuguese.vocabulary ADD CONSTRAINT vocabulary_status_check
  CHECK (status IN (
    'biblioteca',
    'praticando',
    'pronto_para_testar',
    'aprendido'
  ));
