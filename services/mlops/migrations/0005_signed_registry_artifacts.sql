ALTER TABLE model_registry
  ADD COLUMN IF NOT EXISTS signature_algorithm TEXT,
  ADD COLUMN IF NOT EXISTS signature_key_id TEXT,
  ADD COLUMN IF NOT EXISTS artifact_signature TEXT;

UPDATE model_registry
SET
  signature_algorithm = COALESCE(signature_algorithm, ''),
  signature_key_id = COALESCE(signature_key_id, ''),
  artifact_signature = COALESCE(artifact_signature, '');

CREATE INDEX IF NOT EXISTS idx_model_registry_signature_key
  ON model_registry (signature_key_id, created_at DESC);
