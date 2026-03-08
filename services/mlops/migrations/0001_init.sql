CREATE TABLE IF NOT EXISTS model_registry (
  model_name TEXT NOT NULL,
  model_version TEXT NOT NULL,
  dataset_hash TEXT NOT NULL,
  random_seed INTEGER NOT NULL,
  environment_fingerprint TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (model_name, model_version)
);
