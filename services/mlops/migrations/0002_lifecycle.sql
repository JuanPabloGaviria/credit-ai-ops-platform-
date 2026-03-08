CREATE TABLE IF NOT EXISTS ml_training_runs (
  run_id TEXT PRIMARY KEY,
  model_name TEXT NOT NULL,
  dataset_reference TEXT NOT NULL,
  dataset_hash TEXT NOT NULL,
  random_seed INTEGER NOT NULL,
  training_metrics JSONB NOT NULL,
  artifact_uri TEXT NOT NULL,
  artifact_digest TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ml_training_runs_model_created
  ON ml_training_runs (model_name, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ml_training_runs_dataset_hash
  ON ml_training_runs (dataset_hash);

CREATE TABLE IF NOT EXISTS ml_evaluation_runs (
  evaluation_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES ml_training_runs(run_id) ON DELETE CASCADE,
  evaluation_metrics JSONB NOT NULL,
  passed_policy BOOLEAN NOT NULL,
  policy_failures TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ml_evaluation_runs_run_created
  ON ml_evaluation_runs (run_id, created_at DESC);

ALTER TABLE model_registry
  ADD COLUMN IF NOT EXISTS run_id TEXT,
  ADD COLUMN IF NOT EXISTS evaluation_id TEXT,
  ADD COLUMN IF NOT EXISTS training_metrics JSONB,
  ADD COLUMN IF NOT EXISTS evaluation_metrics JSONB,
  ADD COLUMN IF NOT EXISTS model_card_uri TEXT,
  ADD COLUMN IF NOT EXISTS model_card_checksum TEXT,
  ADD COLUMN IF NOT EXISTS environment_snapshot JSONB,
  ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'candidate',
  ADD COLUMN IF NOT EXISTS stage TEXT,
  ADD COLUMN IF NOT EXISTS promoted_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'chk_model_registry_status'
  ) THEN
    ALTER TABLE model_registry
      ADD CONSTRAINT chk_model_registry_status
      CHECK (status IN ('candidate', 'promoted'));
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'chk_model_registry_stage'
  ) THEN
    ALTER TABLE model_registry
      ADD CONSTRAINT chk_model_registry_stage
      CHECK (stage IS NULL OR stage IN ('staging', 'production'));
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_model_registry_status_updated
  ON model_registry (status, updated_at DESC);

CREATE TABLE IF NOT EXISTS mlops_outbox (
  event_id TEXT PRIMARY KEY,
  event_name TEXT NOT NULL,
  payload JSONB NOT NULL,
  published_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mlops_outbox_unpublished
  ON mlops_outbox (created_at ASC)
  WHERE published_at IS NULL;
