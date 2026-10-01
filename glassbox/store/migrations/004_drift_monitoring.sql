CREATE TABLE IF NOT EXISTS drift_baselines (
    baseline_id TEXT PRIMARY KEY NOT NULL CHECK (length(baseline_id) = 26 AND substr(baseline_id, 1, 1) GLOB '[0-7]' AND baseline_id NOT GLOB '*[^0123456789ABCDEFGHJKMNPQRSTVWXYZ]*'),
    agent_name TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    baseline_start TEXT NOT NULL CHECK ((baseline_start GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z' OR (baseline_start GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(baseline_start, 21, 3) != '000') OR (baseline_start GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND substr(baseline_start, 24, 3) != '000')) AND CAST(substr(baseline_start, 1, 4) AS INTEGER) BETWEEN 1 AND 9999 AND CAST(substr(baseline_start, 6, 2) AS INTEGER) BETWEEN 1 AND 12 AND CAST(substr(baseline_start, 9, 2) AS INTEGER) BETWEEN 1 AND 31 AND CAST(substr(baseline_start, 12, 2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(baseline_start, 15, 2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(baseline_start, 18, 2) AS INTEGER) BETWEEN 0 AND 59 AND date(baseline_start) = substr(baseline_start, 1, 10)),
    baseline_end TEXT NOT NULL CHECK ((baseline_end GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z' OR (baseline_end GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(baseline_end, 21, 3) != '000') OR (baseline_end GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND substr(baseline_end, 24, 3) != '000')) AND CAST(substr(baseline_end, 1, 4) AS INTEGER) BETWEEN 1 AND 9999 AND CAST(substr(baseline_end, 6, 2) AS INTEGER) BETWEEN 1 AND 12 AND CAST(substr(baseline_end, 9, 2) AS INTEGER) BETWEEN 1 AND 31 AND CAST(substr(baseline_end, 12, 2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(baseline_end, 15, 2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(baseline_end, 18, 2) AS INTEGER) BETWEEN 0 AND 59 AND date(baseline_end) = substr(baseline_end, 1, 10)),
    created_at TEXT NOT NULL CHECK ((created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z' OR (created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(created_at, 21, 3) != '000') OR (created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND substr(created_at, 24, 3) != '000')) AND CAST(substr(created_at, 1, 4) AS INTEGER) BETWEEN 1 AND 9999 AND CAST(substr(created_at, 6, 2) AS INTEGER) BETWEEN 1 AND 12 AND CAST(substr(created_at, 9, 2) AS INTEGER) BETWEEN 1 AND 31 AND CAST(substr(created_at, 12, 2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at, 15, 2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at, 18, 2) AS INTEGER) BETWEEN 0 AND 59 AND date(created_at) = substr(created_at, 1, 10)),
    version_counts_json TEXT NOT NULL CHECK (json_valid(version_counts_json)),
    reference_json TEXT NOT NULL CHECK (json_valid(reference_json)),
    supersedes_baseline_id TEXT REFERENCES drift_baselines(baseline_id) ON DELETE RESTRICT,
    CHECK (length(trim(agent_name)) > 0),
    CHECK (length(trim(policy_version)) > 0),
    CHECK (length(policy_hash) = 64)
);
CREATE INDEX IF NOT EXISTS idx_drift_baselines_agent_policy ON drift_baselines(agent_name, policy_hash);

CREATE TABLE IF NOT EXISTS drift_runs (
    drift_run_id TEXT PRIMARY KEY NOT NULL CHECK (length(drift_run_id) = 26 AND substr(drift_run_id, 1, 1) GLOB '[0-7]' AND drift_run_id NOT GLOB '*[^0123456789ABCDEFGHJKMNPQRSTVWXYZ]*'),
    baseline_id TEXT NOT NULL REFERENCES drift_baselines(baseline_id) ON DELETE RESTRICT,
    agent_name TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    as_of TEXT NOT NULL CHECK ((as_of GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z' OR (as_of GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(as_of, 21, 3) != '000') OR (as_of GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND substr(as_of, 24, 3) != '000')) AND CAST(substr(as_of, 1, 4) AS INTEGER) BETWEEN 1 AND 9999 AND CAST(substr(as_of, 6, 2) AS INTEGER) BETWEEN 1 AND 12 AND CAST(substr(as_of, 9, 2) AS INTEGER) BETWEEN 1 AND 31 AND CAST(substr(as_of, 12, 2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(as_of, 15, 2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(as_of, 18, 2) AS INTEGER) BETWEEN 0 AND 59 AND date(as_of) = substr(as_of, 1, 10)),
    recent_start TEXT NOT NULL CHECK ((recent_start GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z' OR (recent_start GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(recent_start, 21, 3) != '000') OR (recent_start GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND substr(recent_start, 24, 3) != '000')) AND CAST(substr(recent_start, 1, 4) AS INTEGER) BETWEEN 1 AND 9999 AND CAST(substr(recent_start, 6, 2) AS INTEGER) BETWEEN 1 AND 12 AND CAST(substr(recent_start, 9, 2) AS INTEGER) BETWEEN 1 AND 31 AND CAST(substr(recent_start, 12, 2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(recent_start, 15, 2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(recent_start, 18, 2) AS INTEGER) BETWEEN 0 AND 59 AND date(recent_start) = substr(recent_start, 1, 10)),
    recent_end TEXT NOT NULL CHECK ((recent_end GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z' OR (recent_end GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(recent_end, 21, 3) != '000') OR (recent_end GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND substr(recent_end, 24, 3) != '000')) AND CAST(substr(recent_end, 1, 4) AS INTEGER) BETWEEN 1 AND 9999 AND CAST(substr(recent_end, 6, 2) AS INTEGER) BETWEEN 1 AND 12 AND CAST(substr(recent_end, 9, 2) AS INTEGER) BETWEEN 1 AND 31 AND CAST(substr(recent_end, 12, 2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(recent_end, 15, 2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(recent_end, 18, 2) AS INTEGER) BETWEEN 0 AND 59 AND date(recent_end) = substr(recent_end, 1, 10)),
    status TEXT NOT NULL CHECK (status IN ('healthy','watch','drift_detected','insufficient_data')),
    status_reason TEXT,
    version_counts_json TEXT NOT NULL CHECK (json_valid(version_counts_json)),
    context_json TEXT NOT NULL CHECK (json_valid(context_json))
);
CREATE INDEX IF NOT EXISTS idx_drift_runs_agent_created ON drift_runs(agent_name, as_of);

CREATE TABLE IF NOT EXISTS drift_results (
    drift_result_id TEXT PRIMARY KEY NOT NULL CHECK (length(drift_result_id) = 26 AND substr(drift_result_id, 1, 1) GLOB '[0-7]' AND drift_result_id NOT GLOB '*[^0123456789ABCDEFGHJKMNPQRSTVWXYZ]*'),
    drift_run_id TEXT NOT NULL REFERENCES drift_runs(drift_run_id) ON DELETE CASCADE,
    signal_name TEXT NOT NULL CHECK (signal_name IN ('confidence','decision_type','trace_latency_ms','trace_cost_usd')),
    population TEXT NOT NULL CHECK (population IN ('decision','trace')),
    algorithm TEXT NOT NULL CHECK (algorithm IN ('psi','cusum')),
    status TEXT NOT NULL CHECK (status IN ('healthy','watch','drift_detected','insufficient_data')),
    baseline_count INTEGER NOT NULL CHECK (baseline_count >= 0),
    recent_count INTEGER NOT NULL CHECK (recent_count >= 0),
    metric_value REAL,
    warning_threshold REAL NOT NULL,
    alert_threshold REAL NOT NULL,
    details_json TEXT NOT NULL CHECK (json_valid(details_json)),
    UNIQUE(drift_run_id, signal_name)
);
