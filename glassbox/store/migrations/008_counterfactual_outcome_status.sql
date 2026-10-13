ALTER TABLE counterfactual_results ADD COLUMN outcome_status TEXT NOT NULL
    DEFAULT 'legacy_unrecorded'
    CHECK (outcome_status = 'legacy_unrecorded'
        OR (outcome_status = 'case_failed' AND failure_code IS NOT NULL)
        OR (outcome_status = 'labelled' AND outcome_label IS NOT NULL)
        OR (outcome_status IN ('decision_not_mature', 'outcome_not_applicable')
            AND failure_code IS NULL AND outcome_label IS NULL));
