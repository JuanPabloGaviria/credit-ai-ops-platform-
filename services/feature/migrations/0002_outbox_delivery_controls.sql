ALTER TABLE feature_outbox
  ADD COLUMN IF NOT EXISTS claim_token TEXT,
  ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS claim_expires_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS publish_attempts INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS last_error TEXT,
  ADD COLUMN IF NOT EXISTS dead_lettered_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

UPDATE feature_outbox
SET updated_at = COALESCE(updated_at, created_at);

CREATE INDEX IF NOT EXISTS idx_feature_outbox_dispatchable
  ON feature_outbox (created_at ASC)
  WHERE published_at IS NULL AND dead_lettered_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_feature_outbox_claim_expires
  ON feature_outbox (claim_expires_at)
  WHERE published_at IS NULL AND dead_lettered_at IS NULL;
