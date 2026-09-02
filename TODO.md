# Glassbox Work Queue

This is the living project backlog. Refine an item when new evidence changes its scope; do not mark it complete without a test, artifact, or documented decision.

## P0 — Trace foundation

### P0.1 — Package and event contracts

- [x] Establish Python package metadata, quality tooling, and import-boundary checks.
- [x] Define dependency-neutral canonical trace, span, decision, and evidence events.

### P0.2 — SQLite event store

- [x] Create the complete forward-compatible SQLite schema and first migration.
- [x] Enforce WAL, foreign keys, busy timeout, UTC timestamps, and deletion restrictions.
- [x] Implement typed repository writes and trace-tree reads.
- [x] Add a rebuild migration for databases created before strict UTC RFC3339 storage validation.

### P0.3 — Redaction and content-addressed blobs

- [x] Implement content-addressed, deduplicated blob persistence.
- [x] Prove the redact-before-hash sequence is correct in isolation (`Redactor.apply` then `BlobStore.put`).
- [x] Wire opt-in blob capture into tracing: `span(prompt=..., completion=...)` stores redacted model content and emits `prompt_ref`/`completion_ref`; `capture_input(...)` stores explicit redacted trace input and emits `input_ref` on trace completion. Both operations are fail-open.

### P0.4 — Bounded fail-open collector

- [x] Implement a bounded drop-newest, fail-open collector.
- [x] Expose dropped-event metrics, partial-trace state, flush, and shutdown.

### P0.5 — Public tracing SDK

- [x] Implement sync and async `@trace` with context propagation.
- [x] Implement spans, explicit decision contexts, evidence ownership, and structured citations.
- [x] Verify `GLASSBOX_ENABLED=0` produces zero writes.
- [x] Verify instrumentation failures never change agent returns or exceptions.

### P0.6 — Agent integration and trace inspection

- [x] Integrate one replenishment-agent execution path.
- [x] Produce a redacted sample trace tree.
- [x] Add the read-only CLI trace inspection command.
- [x] Verify the replenishment agent's no-Glassbox fallback by executing a representative `TriageAgent.run` in an environment without Glassbox installed.

### P0.7 — Acceptance gate and approval

- [x] Run the P0 overhead benchmark.
- [x] Write the P0 ergonomics report.
- [x] P0 validation results are recorded; P1 approved on 2026-08-30.

## P1 — Deterministic evaluation

### P1.1 — Evaluation contracts and integration

- [x] Refine P1 into a test-driven implementation plan after the P0 SDK review.
- [x] Add typed golden cases and the `EvaluationTarget` protocol.
- [x] Add the replenishment evaluation adapter under `integrations/`.
- [ ] Revisit the real replenishment agent's decision, evidence, citation, and alternatives mapping after the P1 seed suite establishes end-to-end behavior; refine the domain semantics from observed evaluation results rather than adding an evaluation-only shadow model.

### P1.2 — Deterministic checks and metrics

- [x] Build deterministic schema, evidence, citation, and alternatives assertions.
- [x] Add weighted-kappa agreement and operational metrics.

### P1.3 — Golden suite and CI gate

- [x] Create the 40-case balanced golden set.
- [x] Implement suite gates and CI-compatible exit codes.
- [x] Sanity-check the source-specification `urgency_agreement >= 0.6` gate against the completed 40-case suite using the approved linear weighted-kappa calculation; the scripted suite observed `1.0`, so the `0.6` threshold remains unchanged pending live-provider calibration.
- [ ] Add a non-blocking live-provider smoke mode that reuses the scripted suite contract and cases; keep it outside the deterministic CI gate until its credentials, cost controls, and nondeterminism policy are approved.
- [ ] During P1.3 authoring, inspect the initial do-nothing cases for evidence or alternatives added only to satisfy the uniform evaluation floor; strengthen real-agent reasoning requirements if padding appears, without category exemptions.

## P2 — Decision Cards and feedback (blocked by P1)

### P2.1 — Security baseline and plan

- [ ] Verify loopback port binding before implementation.
- [ ] Refine P2 into a security- and usability-tested implementation plan.

### P2.2 — Read experience

- [ ] Build queue, Decision Card, and trace-view models and templates.

### P2.3 — Feedback workflow

- [ ] Add secure synchronous, append-only, idempotent feedback.

### P2.4 — Export and usability gate

- [ ] Add safe read-only static exports.
- [ ] Run the structured planner usability test.

## P3 — LLM evaluation (deferred)

- [ ] Calibrate the LLM judge against human labels and enforce agreement thresholds.
- [ ] Add drift monitoring and counterfactual providers.

## P4 — Outcome reconciliation (deferred)

- [ ] Implement outcome ingestion and deferred-truth reconciliation.

## P5 — Dashboard and operations (deferred)

- [ ] Add dashboard generation.
- [ ] Wire CI workflow automation.

## Demand-triggered backlog

- [ ] Revisit OTLP runtime export, Parquet retention, and blob garbage collection only when measured demand justifies them.

## Decision log

Record approved scope changes before implementation changes them. Each entry must include date, rationale, affected phase, and approver.

| Date | Decision / scope change | Rationale | Affected phase | Approver |
| --- | --- | --- | --- | --- |
| 2026-08-22 | `EvidenceEvent.evidence_id` is a caller-defined string, unique within its owning `decision_id` (composite key), not a ULID. The spec's "ULIDs for all IDs" applies to system-generated identifiers (`trace_id`, `span_id`, `decision_id`, `override_id`, `outcome_id`, `eval_run_id`) only. | §5's own evidence table already scopes uniqueness "within its decision" — meaningless for a globally-unique ULID. §6's SDK example (`evidence_id="inventory_position"` reused literally in `rationale_citations`) only works ergonomically as a caller-chosen key, not a generated ID the caller would have to capture and thread through. | P0.1, P0.2 | Mohith Kunta |
| 2026-08-28 | `evidence` table's composite key is `(decision_id, evidence_id, field_name)`, not `(decision_id, evidence_id)`. Widened in both `001_initial.sql` and `000_pre_strict_initial.sql` (the two have never differed on anything but timestamp-check strictness, and no external database has ever depended on the narrower key). | `gb.evidence(evidence_id=..., fields={...})` — §6's own canonical example — emits one row per field under one shared `evidence_id`. The 2-column key made every field after the first collide on insert and get silently dropped by the fail-open collector. `evidence_id` is a citation-group key (multiple fields cited together), not a single-row key. | P0.2 | Mohith Kunta |
| 2026-08-30 | P1 is approved. Its first adapter calls the real replenishment agent through a generic `EvaluationTarget` boundary, with a scripted provider for the deterministic gate. P1 also adds minimal real-agent decision/evidence instrumentation so assertions inspect persisted Glassbox data; its domain mapping will be revisited after the seed suite. A live-provider smoke mode is deferred and must reuse that boundary and golden cases. | Exercises real agent orchestration without network, credential, cost, or model-variance dependencies; avoids an evaluation-only shadow decision model while preserving a later domain-semantics refinement. | P1 | Mohith Kunta |
| 2026-08-30 | The independently versioned replenishment agent owns its `integrations/replenishment_triage.py` adapter, `goldens/replenishment_triage/` directory, suite manifest, and real-agent scripted-provider integration tests. Glassbox owns only generic evaluation contracts, runner, metrics, CLI, and fake-target tests. The agent declares Glassbox in a dedicated evaluation extra, not its production dependency set. | §9 expressly permits agent-owned adapters when their import path resolves in the evaluation environment. This preserves the existing optional `agent → glassbox` dependency direction and keeps each self-contained virtualenv free of hidden path manipulation. | P1 | Mohith Kunta |
| 2026-08-30 | All four P1 deterministic assertions apply uniformly to every case category, including do-nothing cases. A no-action decision still requires at least three evidence items and at least one considered-and-rejected alternative. | §2 says evidence is first-class and “No evidence → unexplainable → fails eval,” without an action-based exception. A weak no-action decision risks silently missing an exception; any resulting evidentiary padding is an agent-reasoning defect to investigate, not grounds for an evaluation exemption. | P1 | Mohith Kunta |
| 2026-08-30 | Urgency agreement uses linear weighted kappa over the ordered real-agent SLA scale `LOW < MEDIUM < HIGH < CRITICAL`. The source-specification `urgency_agreement >= 0.6` threshold remains unchanged until it is checked against completed 40-case linear-kappa results. | Adjacent priority tiers have distinct operational response windows, so their misses must remain visible; quadratic weighting would disproportionately forgive systematic adjacent-tier SLA failures. | P1 | Mohith Kunta |
| 2026-08-31 | The generic `glassbox.eval` runner does not create traces or collectors. Each agent-owned `EvaluationTarget` creates an isolated per-case trace/collector, flushes it, reads its persisted decision data, and returns a generic `DecisionResult`. | The approved `eval-dependencies` import boundary forbids `sdk` and `collector` imports, so a runner-owned tracing lifecycle would contradict the architecture. This preserves both isolation and the dependency rule. | P1 | Mohith Kunta |
