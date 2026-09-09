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
