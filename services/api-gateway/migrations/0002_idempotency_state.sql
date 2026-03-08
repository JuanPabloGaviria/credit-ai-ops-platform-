ALTER TABLE idempotency_keys
  ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending',
  ADD COLUMN IF NOT EXISTS response_status_code INTEGER,
  ADD COLUMN IF NOT EXISTS error_payload JSONB,
  ADD COLUMN IF NOT EXISTS error_status_code INTEGER,
  ADD COLUMN IF NOT EXISTS locked_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

UPDATE idempotency_keys
SET
  status = CASE
    WHEN response_payload IS NOT NULL THEN 'completed'
    ELSE 'pending'
  END,
  response_status_code = COALESCE(response_status_code, CASE WHEN response_payload IS NOT NULL THEN 200 END),
  locked_at = COALESCE(locked_at, created_at),
  expires_at = COALESCE(expires_at, created_at + INTERVAL '5 minutes'),
  completed_at = COALESCE(completed_at, CASE WHEN response_payload IS NOT NULL THEN created_at END),
  updated_at = COALESCE(updated_at, NOW());

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'chk_idempotency_status'
  ) THEN
    ALTER TABLE idempotency_keys
      ADD CONSTRAINT chk_idempotency_status
      CHECK (status IN ('pending', 'completed', 'failed'));
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_idempotency_status_locked
  ON idempotency_keys (status, locked_at);

CREATE INDEX IF NOT EXISTS idx_idempotency_expires_at
  ON idempotency_keys (expires_at);
