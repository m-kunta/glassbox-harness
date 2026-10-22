ALTER TABLE counterfactual_runs ADD COLUMN execution_mode TEXT NOT NULL
    DEFAULT 'offline'
    CHECK (execution_mode IN ('offline', 'live_shadow'));
