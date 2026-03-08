ALTER TABLE audit_events
  ADD COLUMN IF NOT EXISTS correlation_id TEXT,
  ADD COLUMN IF NOT EXISTS causation_id TEXT;

UPDATE audit_events
SET correlation_id = COALESCE(correlation_id, trace_id, event_id)
WHERE correlation_id IS NULL;

ALTER TABLE audit_events
  ALTER COLUMN correlation_id SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_audit_events_correlation_created
  ON audit_events (correlation_id, created_at DESC);
