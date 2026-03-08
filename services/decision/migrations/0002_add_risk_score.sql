DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM pg_class
    WHERE relname = 'credit_decisions'
      AND relkind = 'r'
  ) THEN
    ALTER TABLE credit_decisions
      ADD COLUMN IF NOT EXISTS risk_score NUMERIC;

    UPDATE credit_decisions
    SET risk_score = 0
    WHERE risk_score IS NULL;

    ALTER TABLE credit_decisions
      ALTER COLUMN risk_score SET NOT NULL;
  END IF;
END $$;
