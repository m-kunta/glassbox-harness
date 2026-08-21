# Glassbox Work Queue

This is the living project backlog. Refine an item when new evidence changes its scope; do not mark it complete without a test, artifact, or documented decision.

## P0 — Trace foundation

- [ ] Establish Python package metadata, quality tooling, and import-boundary checks.
- [ ] Define dependency-neutral canonical trace, span, decision, and evidence events.
- [ ] Create the complete forward-compatible SQLite schema and first migration.
- [ ] Enforce WAL, foreign keys, busy timeout, UTC timestamps, and deletion restrictions.
- [ ] Implement typed repository writes and trace-tree reads.
- [ ] Implement content-addressed, deduplicated blob persistence.
- [ ] Run configured redaction hooks before hashing or persistence.
- [ ] Implement a bounded drop-newest, fail-open collector.
- [ ] Expose dropped-event metrics, partial-trace state, flush, and shutdown.
- [ ] Implement sync and async `@trace` with context propagation.
- [ ] Implement spans, explicit decision contexts, evidence ownership, and structured citations.
- [ ] Verify `GLASSBOX_ENABLED=0` produces zero writes.
- [ ] Verify instrumentation failures never change agent returns or exceptions.
- [ ] Integrate one replenishment-agent execution path.
- [ ] Produce a redacted sample trace tree.
- [ ] Run the P0 overhead benchmark.
- [ ] Write the P0 ergonomics report and stop for approval.

## P1 — Deterministic evaluation (blocked by P0 approval)

- [ ] Refine P1 into a test-driven implementation plan after the P0 SDK review.
- [ ] Add typed golden cases and the `EvaluationTarget` protocol.
- [ ] Add the replenishment evaluation adapter under `integrations/`.
- [ ] Build deterministic schema, evidence, citation, and alternatives assertions.
- [ ] Add weighted-kappa agreement and operational metrics.
- [ ] Create the 40-case balanced golden set.
- [ ] Implement suite gates and CI-compatible exit codes.

## P2 — Decision Cards and feedback (blocked by P1)

- [ ] Verify loopback port binding before implementation.
- [ ] Refine P2 into a security- and usability-tested implementation plan.
- [ ] Build queue, Decision Card, and trace-view models and templates.
- [ ] Add secure synchronous, append-only, idempotent feedback.
- [ ] Add safe read-only static exports.
- [ ] Run the structured planner usability test.

## Deferred P3–P5

- [ ] LLM judge calibration, drift monitoring, and counterfactual providers.
- [ ] Outcome ingestion and deferred-truth reconciliation.
- [ ] Dashboard generation and GitHub Actions workflow wiring.
- [ ] Revisit OTLP runtime export, Parquet retention, and blob garbage collection from measured demand.

## Decisions and refinements

- [ ] Record newly approved scope changes here with date, rationale, and affected phase before changing implementation.
