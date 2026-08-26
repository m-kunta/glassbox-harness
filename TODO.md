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
- [ ] Add a rebuild migration for databases created before strict UTC RFC3339 storage validation.

### P0.3 — Redaction and content-addressed blobs

- [x] Implement content-addressed, deduplicated blob persistence.
- [x] Run configured redaction hooks before hashing or persistence.

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
- [ ] Verify the replenishment agent's no-Glassbox fallback by executing a representative `TriageAgent.run` in an environment without Glassbox installed.

### P0.7 — Acceptance gate and approval

- [ ] Run the P0 overhead benchmark.
- [ ] Write the P0 ergonomics report.
- [ ] Record validation results and stop for explicit P1 approval.

## P1 — Deterministic evaluation (blocked by P0 approval)

### P1.1 — Evaluation contracts and integration

- [ ] Refine P1 into a test-driven implementation plan after the P0 SDK review.
- [ ] Add typed golden cases and the `EvaluationTarget` protocol.
- [ ] Add the replenishment evaluation adapter under `integrations/`.

### P1.2 — Deterministic checks and metrics

- [ ] Build deterministic schema, evidence, citation, and alternatives assertions.
- [ ] Add weighted-kappa agreement and operational metrics.

### P1.3 — Golden suite and CI gate

- [ ] Create the 40-case balanced golden set.
- [ ] Implement suite gates and CI-compatible exit codes.

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
