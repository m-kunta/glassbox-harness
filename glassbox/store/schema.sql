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
    PRIMARY KEY (decision_id, evidence_id, field_name)
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

CREATE TABLE IF NOT EXISTS feedback (
    feedback_id TEXT PRIMARY KEY NOT NULL CHECK (
        length(feedback_id) = 26
        AND substr(feedback_id, 1, 1) GLOB '[0-7]'
        AND feedback_id NOT GLOB '*[^0123456789ABCDEFGHJKMNPQRSTVWXYZ]*'
    ),
    decision_id TEXT NOT NULL REFERENCES decisions(decision_id) ON DELETE RESTRICT,
    verdict TEXT NOT NULL CHECK (verdict IN ('agree', 'disagree', 'uncertain')),
    reason_code TEXT,
    free_text TEXT,
    corrected_recommendation TEXT CHECK (
        corrected_recommendation IS NULL OR json_valid(corrected_recommendation)
    ),
    created_at TEXT NOT NULL CHECK (
        (
            created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
            OR (created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(created_at, 21, 3) != '000')
            OR (created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND substr(created_at, 24, 3) != '000')
        )
        AND datetime(created_at) IS NOT NULL
    ),
    idempotency_key TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS feedback (
    feedback_id TEXT PRIMARY KEY NOT NULL CHECK (
        length(feedback_id) = 26
        AND substr(feedback_id, 1, 1) GLOB '[0-7]'
        AND feedback_id NOT GLOB '*[^0123456789ABCDEFGHJKMNPQRSTVWXYZ]*'
    ),
    decision_id TEXT NOT NULL REFERENCES decisions(decision_id) ON DELETE RESTRICT,
    verdict TEXT NOT NULL CHECK (verdict IN ('agree', 'disagree', 'uncertain')),
    reason_code TEXT,
    free_text TEXT,
    corrected_recommendation TEXT CHECK (
        corrected_recommendation IS NULL OR json_valid(corrected_recommendation)
    ),
    created_at TEXT NOT NULL CHECK (
        (
            created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
            OR (created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(created_at, 21, 3) != '000')
            OR (created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND substr(created_at, 24, 3) != '000')
        )
        AND datetime(created_at) IS NOT NULL
    ),
    idempotency_key TEXT NOT NULL UNIQUE,
    reasoning_quality_score INTEGER CHECK (reasoning_quality_score BETWEEN 1 AND 5),
    reasoning_quality_rubric_version TEXT,
    CHECK ((reasoning_quality_score IS NULL) = (reasoning_quality_rubric_version IS NULL))
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
    run_at TEXT NOT NULL CHECK ((run_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z' OR (run_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(run_at, 21, 3) != '000') OR (run_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND substr(run_at, 24, 3) != '000')) AND CAST(substr(run_at, 1, 4) AS INTEGER) BETWEEN 1 AND 9999 AND CAST(substr(run_at, 6, 2) AS INTEGER) BETWEEN 1 AND 12 AND CAST(substr(run_at, 9, 2) AS INTEGER) BETWEEN 1 AND 31 AND CAST(substr(run_at, 12, 2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(run_at, 15, 2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(run_at, 18, 2) AS INTEGER) BETWEEN 0 AND 59 AND date(run_at) = substr(run_at, 1, 10)),
    run_kind TEXT NOT NULL CHECK (run_kind IN ('deterministic', 'judge')),
    judge_provider TEXT,
    judge_model TEXT,
    rubric_version TEXT,
    judge_temperature REAL,
    self_judge_allowed INTEGER,
    status TEXT,
    status_reason TEXT,
    judge_failure_count INTEGER,
    CHECK (
      (run_kind = 'deterministic' AND judge_provider IS NULL AND judge_model IS NULL
       AND rubric_version IS NULL AND judge_temperature IS NULL AND self_judge_allowed IS NULL
       AND status IS NULL AND status_reason IS NULL AND judge_failure_count IS NULL)
      OR
      (run_kind = 'judge' AND judge_provider IS NOT NULL AND judge_model IS NOT NULL
       AND rubric_version IS NOT NULL
       AND judge_temperature IS NOT NULL AND judge_temperature = 0
       AND self_judge_allowed IS NOT NULL AND self_judge_allowed IN (0, 1)
       AND status IS NOT NULL AND status IN ('passed', 'failed', 'uncalibrated')
       AND status_reason IS NOT NULL
       AND judge_failure_count IS NOT NULL AND judge_failure_count >= 0)
    )
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
    run_at TEXT NOT NULL CHECK ((run_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z' OR (run_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(run_at, 21, 3) != '000') OR (run_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND substr(run_at, 24, 3) != '000')) AND CAST(substr(run_at, 1, 4) AS INTEGER) BETWEEN 1 AND 9999 AND CAST(substr(run_at, 6, 2) AS INTEGER) BETWEEN 1 AND 12 AND CAST(substr(run_at, 9, 2) AS INTEGER) BETWEEN 1 AND 31 AND CAST(substr(run_at, 12, 2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(run_at, 15, 2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(run_at, 18, 2) AS INTEGER) BETWEEN 0 AND 59 AND date(run_at) = substr(run_at, 1, 10)),
    decision_id TEXT REFERENCES decisions(decision_id) ON DELETE RESTRICT,
    self_judge_bypassed INTEGER NOT NULL DEFAULT 0 CHECK (self_judge_bypassed IN (0, 1))
);

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
