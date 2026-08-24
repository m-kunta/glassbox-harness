CREATE TABLE IF NOT EXISTS traces (
    trace_id TEXT PRIMARY KEY NOT NULL CHECK (
        length(trace_id) = 26
        AND substr(trace_id, 1, 1) GLOB '[0-7]'
        AND trace_id NOT GLOB '*[^0123456789ABCDEFGHJKMNPQRSTVWXYZ]*'
    ),
    agent_name TEXT NOT NULL,
    agent_version TEXT NOT NULL,
    started_at TEXT NOT NULL CHECK ((started_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z' OR (started_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(started_at, 21, 3) != '000') OR (started_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND substr(started_at, 24, 3) != '000')) AND CAST(substr(started_at, 1, 4) AS INTEGER) BETWEEN 1 AND 9999 AND CAST(substr(started_at, 6, 2) AS INTEGER) BETWEEN 1 AND 12 AND CAST(substr(started_at, 9, 2) AS INTEGER) BETWEEN 1 AND 31 AND CAST(substr(started_at, 12, 2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(started_at, 15, 2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(started_at, 18, 2) AS INTEGER) BETWEEN 0 AND 59 AND date(started_at) = substr(started_at, 1, 10)),
    ended_at TEXT CHECK (ended_at IS NULL OR ((ended_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z' OR (ended_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(ended_at, 21, 3) != '000') OR (ended_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND substr(ended_at, 24, 3) != '000')) AND CAST(substr(ended_at, 1, 4) AS INTEGER) BETWEEN 1 AND 9999 AND CAST(substr(ended_at, 6, 2) AS INTEGER) BETWEEN 1 AND 12 AND CAST(substr(ended_at, 9, 2) AS INTEGER) BETWEEN 1 AND 31 AND CAST(substr(ended_at, 12, 2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(ended_at, 15, 2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(ended_at, 18, 2) AS INTEGER) BETWEEN 0 AND 59 AND date(ended_at) = substr(ended_at, 1, 10))),
    status TEXT NOT NULL CHECK (status IN ('ok', 'error', 'partial')),
    environment TEXT NOT NULL CHECK (environment IN ('dev', 'shadow', 'prod')),
    input_ref TEXT,
    total_tokens INTEGER CHECK (total_tokens IS NULL OR total_tokens >= 0),
    total_cost_usd REAL CHECK (total_cost_usd IS NULL OR total_cost_usd >= 0),
    latency_ms REAL CHECK (latency_ms IS NULL OR latency_ms >= 0),
    attributes TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(attributes))
);

CREATE TABLE IF NOT EXISTS spans (
    span_id TEXT PRIMARY KEY NOT NULL CHECK (
        length(span_id) = 26
        AND substr(span_id, 1, 1) GLOB '[0-7]'
        AND span_id NOT GLOB '*[^0123456789ABCDEFGHJKMNPQRSTVWXYZ]*'
    ),
    trace_id TEXT NOT NULL REFERENCES traces(trace_id) ON DELETE CASCADE,
    parent_span_id TEXT REFERENCES spans(span_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    span_kind TEXT NOT NULL CHECK (span_kind IN ('llm', 'retrieval', 'tool', 'compute')),
    started_at TEXT NOT NULL CHECK ((started_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z' OR (started_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(started_at, 21, 3) != '000') OR (started_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND substr(started_at, 24, 3) != '000')) AND CAST(substr(started_at, 1, 4) AS INTEGER) BETWEEN 1 AND 9999 AND CAST(substr(started_at, 6, 2) AS INTEGER) BETWEEN 1 AND 12 AND CAST(substr(started_at, 9, 2) AS INTEGER) BETWEEN 1 AND 31 AND CAST(substr(started_at, 12, 2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(started_at, 15, 2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(started_at, 18, 2) AS INTEGER) BETWEEN 0 AND 59 AND date(started_at) = substr(started_at, 1, 10)),
    ended_at TEXT CHECK (ended_at IS NULL OR ((ended_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z' OR (ended_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(ended_at, 21, 3) != '000') OR (ended_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND substr(ended_at, 24, 3) != '000')) AND CAST(substr(ended_at, 1, 4) AS INTEGER) BETWEEN 1 AND 9999 AND CAST(substr(ended_at, 6, 2) AS INTEGER) BETWEEN 1 AND 12 AND CAST(substr(ended_at, 9, 2) AS INTEGER) BETWEEN 1 AND 31 AND CAST(substr(ended_at, 12, 2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(ended_at, 15, 2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(ended_at, 18, 2) AS INTEGER) BETWEEN 0 AND 59 AND date(ended_at) = substr(ended_at, 1, 10))),
    attributes TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(attributes)),
    prompt_ref TEXT,
    completion_ref TEXT,
    model TEXT,
    temperature REAL,
    tokens_in INTEGER CHECK (tokens_in IS NULL OR tokens_in >= 0),
    tokens_out INTEGER CHECK (tokens_out IS NULL OR tokens_out >= 0),
    latency_ms REAL CHECK (latency_ms IS NULL OR latency_ms >= 0)
);

CREATE TABLE IF NOT EXISTS decisions (
    decision_id TEXT PRIMARY KEY NOT NULL CHECK (
        length(decision_id) = 26
        AND substr(decision_id, 1, 1) GLOB '[0-7]'
        AND decision_id NOT GLOB '*[^0123456789ABCDEFGHJKMNPQRSTVWXYZ]*'
    ),
    trace_id TEXT NOT NULL REFERENCES traces(trace_id) ON DELETE CASCADE,
    agent_name TEXT NOT NULL,
    agent_version TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    decision_type TEXT NOT NULL,
    recommendation TEXT NOT NULL CHECK (json_valid(recommendation)),
    rationale TEXT NOT NULL,
    rationale_citations TEXT NOT NULL CHECK (json_valid(rationale_citations)),
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    alternatives_considered TEXT NOT NULL CHECK (json_valid(alternatives_considered)),
    decided_at TEXT NOT NULL CHECK ((decided_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z' OR (decided_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(decided_at, 21, 3) != '000') OR (decided_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND substr(decided_at, 24, 3) != '000')) AND CAST(substr(decided_at, 1, 4) AS INTEGER) BETWEEN 1 AND 9999 AND CAST(substr(decided_at, 6, 2) AS INTEGER) BETWEEN 1 AND 12 AND CAST(substr(decided_at, 9, 2) AS INTEGER) BETWEEN 1 AND 31 AND CAST(substr(decided_at, 12, 2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(decided_at, 15, 2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(decided_at, 18, 2) AS INTEGER) BETWEEN 0 AND 59 AND date(decided_at) = substr(decided_at, 1, 10))
);

CREATE INDEX IF NOT EXISTS idx_decisions_agent_decided_at ON decisions(agent_name, decided_at);
CREATE INDEX IF NOT EXISTS idx_decisions_entity ON decisions(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_decisions_type_confidence ON decisions(decision_type, confidence);

CREATE TABLE IF NOT EXISTS evidence (
    decision_id TEXT NOT NULL REFERENCES decisions(decision_id) ON DELETE CASCADE,
    evidence_id TEXT NOT NULL,
    source_system TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    field_name TEXT NOT NULL,
    field_value_json TEXT NOT NULL CHECK (json_valid(field_value_json)),
    weight REAL NOT NULL,
    retrieved_at TEXT NOT NULL CHECK ((retrieved_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z' OR (retrieved_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(retrieved_at, 21, 3) != '000') OR (retrieved_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND substr(retrieved_at, 24, 3) != '000')) AND CAST(substr(retrieved_at, 1, 4) AS INTEGER) BETWEEN 1 AND 9999 AND CAST(substr(retrieved_at, 6, 2) AS INTEGER) BETWEEN 1 AND 12 AND CAST(substr(retrieved_at, 9, 2) AS INTEGER) BETWEEN 1 AND 31 AND CAST(substr(retrieved_at, 12, 2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(retrieved_at, 15, 2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(retrieved_at, 18, 2) AS INTEGER) BETWEEN 0 AND 59 AND date(retrieved_at) = substr(retrieved_at, 1, 10)),
    PRIMARY KEY (decision_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS overrides (
    override_id TEXT PRIMARY KEY NOT NULL CHECK (
        length(override_id) = 26
        AND substr(override_id, 1, 1) GLOB '[0-7]'
        AND override_id NOT GLOB '*[^0123456789ABCDEFGHJKMNPQRSTVWXYZ]*'
    ),
    decision_id TEXT NOT NULL REFERENCES decisions(decision_id) ON DELETE RESTRICT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('accepted', 'modified', 'rejected')),
    modified_value TEXT CHECK (modified_value IS NULL OR json_valid(modified_value)),
    reason_code TEXT,
    free_text TEXT,
    created_at TEXT NOT NULL CHECK ((created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z' OR (created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(created_at, 21, 3) != '000') OR (created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND substr(created_at, 24, 3) != '000')) AND CAST(substr(created_at, 1, 4) AS INTEGER) BETWEEN 1 AND 9999 AND CAST(substr(created_at, 6, 2) AS INTEGER) BETWEEN 1 AND 12 AND CAST(substr(created_at, 9, 2) AS INTEGER) BETWEEN 1 AND 31 AND CAST(substr(created_at, 12, 2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at, 15, 2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at, 18, 2) AS INTEGER) BETWEEN 0 AND 59 AND date(created_at) = substr(created_at, 1, 10)),
    supersedes_override_id TEXT REFERENCES overrides(override_id) ON DELETE RESTRICT,
    idempotency_key TEXT NOT NULL UNIQUE
);

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
    label TEXT CHECK (label IS NULL OR label IN ('tp', 'fp', 'tn', 'fn'))
);

CREATE TABLE IF NOT EXISTS eval_runs (
    eval_run_id TEXT PRIMARY KEY NOT NULL CHECK (
        length(eval_run_id) = 26
        AND substr(eval_run_id, 1, 1) GLOB '[0-7]'
        AND eval_run_id NOT GLOB '*[^0123456789ABCDEFGHJKMNPQRSTVWXYZ]*'
    ),
    suite_id TEXT NOT NULL,
    suite_version TEXT NOT NULL,
    agent_version TEXT NOT NULL,
    run_at TEXT NOT NULL CHECK ((run_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z' OR (run_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(run_at, 21, 3) != '000') OR (run_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND substr(run_at, 24, 3) != '000')) AND CAST(substr(run_at, 1, 4) AS INTEGER) BETWEEN 1 AND 9999 AND CAST(substr(run_at, 6, 2) AS INTEGER) BETWEEN 1 AND 12 AND CAST(substr(run_at, 9, 2) AS INTEGER) BETWEEN 1 AND 31 AND CAST(substr(run_at, 12, 2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(run_at, 15, 2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(run_at, 18, 2) AS INTEGER) BETWEEN 0 AND 59 AND date(run_at) = substr(run_at, 1, 10))
);

CREATE TABLE IF NOT EXISTS eval_results (
    eval_result_id TEXT PRIMARY KEY NOT NULL CHECK (
        length(eval_result_id) = 26
        AND substr(eval_result_id, 1, 1) GLOB '[0-7]'
        AND eval_result_id NOT GLOB '*[^0123456789ABCDEFGHJKMNPQRSTVWXYZ]*'
    ),
    eval_run_id TEXT NOT NULL REFERENCES eval_runs(eval_run_id) ON DELETE CASCADE,
    case_id TEXT NOT NULL,
    assertion_name TEXT NOT NULL,
    passed INTEGER NOT NULL CHECK (passed IN (0, 1)),
    score REAL,
    judge_rationale TEXT,
    run_at TEXT NOT NULL CHECK ((run_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z' OR (run_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(run_at, 21, 3) != '000') OR (run_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND substr(run_at, 24, 3) != '000')) AND CAST(substr(run_at, 1, 4) AS INTEGER) BETWEEN 1 AND 9999 AND CAST(substr(run_at, 6, 2) AS INTEGER) BETWEEN 1 AND 12 AND CAST(substr(run_at, 9, 2) AS INTEGER) BETWEEN 1 AND 31 AND CAST(substr(run_at, 12, 2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(run_at, 15, 2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(run_at, 18, 2) AS INTEGER) BETWEEN 0 AND 59 AND date(run_at) = substr(run_at, 1, 10))
);
