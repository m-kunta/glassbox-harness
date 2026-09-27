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
       AND rubric_version IS NOT NULL AND judge_temperature = 0
       AND self_judge_allowed IN (0, 1) AND status IN ('passed', 'failed', 'uncalibrated')
       AND status_reason IS NOT NULL AND judge_failure_count >= 0)
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
