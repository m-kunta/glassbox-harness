CREATE TABLE IF NOT EXISTS outcomes (
    outcome_id TEXT PRIMARY KEY NOT NULL CHECK (
        length(outcome_id) = 26
        AND substr(outcome_id, 1, 1) GLOB '[0-7]'
        AND outcome_id NOT GLOB '*[^0123456789ABCDEFGHJKMNPQRSTVWXYZ]*'
    ),
    decision_id TEXT NOT NULL REFERENCES decisions(decision_id) ON DELETE RESTRICT,
    outcome_type TEXT NOT NULL,
    observed_at TEXT NOT NULL CHECK ((observed_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z' OR (observed_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(observed_at, 21, 3) != '000') OR (observed_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND substr(observed_at, 24, 3) != '000')) AND CAST(substr(observed_at, 1, 4) AS INTEGER) BETWEEN 1 AND 9999 AND CAST(substr(observed_at, 6, 2) AS INTEGER) BETWEEN 1 AND 12 AND CAST(substr(observed_at, 9, 2) AS INTEGER) BETWEEN 1 AND 31 AND CAST(substr(observed_at, 12, 2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(observed_at, 15, 2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(observed_at, 18, 2) AS INTEGER) BETWEEN 0 AND 59 AND date(observed_at) = substr(observed_at, 1, 10)),
    horizon_days INTEGER NOT NULL CHECK (horizon_days >= 0),
    value TEXT NOT NULL CHECK (json_valid(value)),
    label TEXT CHECK (label IS NULL OR label IN ('tp', 'fp', 'tn', 'fn')),
    source_id TEXT,
    reconciliation_policy_version TEXT,
    reconciliation_policy_hash TEXT,
    CHECK ((source_id IS NULL) = (reconciliation_policy_version IS NULL)),
    CHECK ((source_id IS NULL) = (reconciliation_policy_hash IS NULL)),
    CHECK (reconciliation_policy_hash IS NULL OR length(reconciliation_policy_hash) = 64)
);
CREATE UNIQUE INDEX idx_outcomes_source_id ON outcomes(source_id) WHERE source_id IS NOT NULL;
