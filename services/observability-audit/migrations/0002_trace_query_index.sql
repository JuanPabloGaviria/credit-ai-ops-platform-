CREATE INDEX IF NOT EXISTS idx_audit_events_trace_created
  ON audit_events (trace_id, created_at DESC)
  WHERE trace_id IS NOT NULL;
