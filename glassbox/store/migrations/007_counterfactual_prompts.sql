CREATE TABLE IF NOT EXISTS counterfactual_runs (
    run_id TEXT PRIMARY KEY NOT NULL CHECK (length(run_id) = 26 AND substr(run_id,1,1) GLOB '[0-7]' AND run_id NOT GLOB '*[^0123456789ABCDEFGHJKMNPQRSTVWXYZ]*'),
    created_at TEXT NOT NULL CHECK ((created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z' OR (created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(created_at,21,3) != '000') OR (created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND substr(created_at,24,3) != '000')) AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND CAST(substr(created_at,6,2) AS INTEGER) BETWEEN 1 AND 12 AND CAST(substr(created_at,9,2) AS INTEGER) BETWEEN 1 AND 31 AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59 AND date(created_at) = substr(created_at,1,10)),
    as_of TEXT NOT NULL CHECK ((as_of GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z' OR (as_of GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(as_of,21,3) != '000') OR (as_of GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND substr(as_of,24,3) != '000')) AND CAST(substr(as_of,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND CAST(substr(as_of,6,2) AS INTEGER) BETWEEN 1 AND 12 AND CAST(substr(as_of,9,2) AS INTEGER) BETWEEN 1 AND 31 AND CAST(substr(as_of,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(as_of,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(as_of,18,2) AS INTEGER) BETWEEN 0 AND 59 AND date(as_of) = substr(as_of,1,10)),
    decided_since TEXT NOT NULL CHECK ((decided_since GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z' OR (decided_since GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(decided_since,21,3) != '000') OR (decided_since GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND substr(decided_since,24,3) != '000')) AND CAST(substr(decided_since,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND CAST(substr(decided_since,6,2) AS INTEGER) BETWEEN 1 AND 12 AND CAST(substr(decided_since,9,2) AS INTEGER) BETWEEN 1 AND 31 AND CAST(substr(decided_since,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(decided_since,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(decided_since,18,2) AS INTEGER) BETWEEN 0 AND 59 AND date(decided_since) = substr(decided_since,1,10)),
    provider TEXT NOT NULL CHECK (length(trim(provider)) > 0),
    model TEXT NOT NULL CHECK (length(trim(model)) > 0),
    base_url TEXT NOT NULL CHECK (length(trim(base_url)) > 0),
    prompt_version TEXT NOT NULL CHECK (length(trim(prompt_version)) > 0),
    prompt_hash TEXT NOT NULL CHECK (length(prompt_hash) = 64 AND prompt_hash NOT GLOB '*[^0123456789abcdef]*'),
    outcome_policy_version TEXT NOT NULL CHECK (length(trim(outcome_policy_version)) > 0),
    outcome_policy_hash TEXT NOT NULL CHECK (length(outcome_policy_hash) = 64 AND outcome_policy_hash NOT GLOB '*[^0123456789abcdef]*'),
    selected_count INTEGER NOT NULL CHECK (typeof(selected_count) = 'integer' AND selected_count >= 0),
    succeeded_count INTEGER NOT NULL CHECK (typeof(succeeded_count) = 'integer' AND succeeded_count >= 0),
    failed_count INTEGER NOT NULL CHECK (typeof(failed_count) = 'integer' AND failed_count >= 0),
    remote_egress_confirmed INTEGER NOT NULL CHECK (remote_egress_confirmed IN (0,1)),
    CHECK (selected_count = succeeded_count + failed_count)
);
CREATE TABLE IF NOT EXISTS counterfactual_results (
    result_id TEXT PRIMARY KEY NOT NULL CHECK (length(result_id) = 26 AND substr(result_id,1,1) GLOB '[0-7]' AND result_id NOT GLOB '*[^0123456789ABCDEFGHJKMNPQRSTVWXYZ]*'),
    run_id TEXT NOT NULL REFERENCES counterfactual_runs(run_id) ON DELETE RESTRICT,
    decision_id TEXT NOT NULL REFERENCES decisions(decision_id) ON DELETE RESTRICT,
    selected_order INTEGER NOT NULL CHECK (typeof(selected_order) = 'integer' AND selected_order >= 0),
    created_at TEXT NOT NULL CHECK ((created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z' OR (created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(created_at,21,3) != '000') OR (created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND substr(created_at,24,3) != '000')) AND CAST(substr(created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 AND CAST(substr(created_at,6,2) AS INTEGER) BETWEEN 1 AND 12 AND CAST(substr(created_at,9,2) AS INTEGER) BETWEEN 1 AND 31 AND CAST(substr(created_at,12,2) AS INTEGER) BETWEEN 0 AND 23 AND CAST(substr(created_at,15,2) AS INTEGER) BETWEEN 0 AND 59 AND CAST(substr(created_at,18,2) AS INTEGER) BETWEEN 0 AND 59 AND date(created_at) = substr(created_at,1,10)),
    snapshot_facts_json TEXT NOT NULL CHECK (json_valid(snapshot_facts_json) AND json(snapshot_facts_json) = snapshot_facts_json),
    capture_references_json TEXT NOT NULL CHECK (json_valid(capture_references_json) AND json(capture_references_json) = capture_references_json),
    source_model_provenance_json TEXT NOT NULL CHECK (json_valid(source_model_provenance_json) AND json(source_model_provenance_json) = source_model_provenance_json),
    model_parity TEXT NOT NULL CHECK (model_parity IN ('matched','different','unknown')),
    input_hash TEXT CHECK (input_hash IS NULL OR (length(input_hash) = 64 AND input_hash NOT GLOB '*[^0123456789abcdef]*')),
    original_recommendation_json TEXT NOT NULL CHECK (json_valid(original_recommendation_json) AND json(original_recommendation_json) = original_recommendation_json),
    original_rationale TEXT NOT NULL,
    hypothetical_recommendation_json TEXT CHECK (hypothetical_recommendation_json IS NULL OR (json_valid(hypothetical_recommendation_json) AND json(hypothetical_recommendation_json) = hypothetical_recommendation_json)),
    hypothetical_rationale TEXT,
    failure_code TEXT,
    outcome_id TEXT REFERENCES outcomes(outcome_id) ON DELETE RESTRICT,
    outcome_label TEXT CHECK (outcome_label IS NULL OR outcome_label IN ('tp','fp','tn','fn')),
    CHECK ((outcome_id IS NULL) = (outcome_label IS NULL)),
    CHECK ((failure_code IS NULL AND input_hash IS NOT NULL AND hypothetical_recommendation_json IS NOT NULL AND hypothetical_rationale IS NOT NULL AND length(trim(hypothetical_rationale)) > 0)
        OR (failure_code IS NOT NULL AND length(trim(failure_code)) > 0 AND hypothetical_recommendation_json IS NULL AND hypothetical_rationale IS NULL AND outcome_id IS NULL)),
    UNIQUE(run_id, decision_id),
    UNIQUE(run_id, selected_order)
);
CREATE INDEX IF NOT EXISTS idx_counterfactual_runs_created ON counterfactual_runs(created_at);
CREATE INDEX IF NOT EXISTS idx_counterfactual_results_decision ON counterfactual_results(decision_id);
CREATE TRIGGER counterfactual_runs_no_update BEFORE UPDATE ON counterfactual_runs
BEGIN
    SELECT RAISE(ABORT, 'counterfactual history is immutable');
END;
CREATE TRIGGER counterfactual_runs_no_delete BEFORE DELETE ON counterfactual_runs
BEGIN
    SELECT RAISE(ABORT, 'counterfactual history is immutable');
END;
CREATE TRIGGER counterfactual_results_no_update BEFORE UPDATE ON counterfactual_results
BEGIN
    SELECT RAISE(ABORT, 'counterfactual history is immutable');
END;
CREATE TRIGGER counterfactual_results_no_delete BEFORE DELETE ON counterfactual_results
BEGIN
    SELECT RAISE(ABORT, 'counterfactual history is immutable');
END;
