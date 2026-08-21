# Glassbox P0–P2 Design

**Approved source:** `/Users/MKunta/Downloads/glassbox-spec-v1.2.md`

Glassbox is a local-first Python harness for tracing, evaluating, and explaining supply-chain agent decisions. The first release is a modular monolith delivered as three gated increments: P0 tracing and persistence, P1 deterministic evaluation, and P2 planner-readable Decision Cards with durable feedback.

P0 uses dependency-neutral event models, a non-invasive SDK, a bounded fail-open telemetry collector, SQLite in WAL mode, and content-addressed blobs. Evidence belongs to an explicit decision context and rationale citations resolve to evidence IDs. P0 ends with a real-agent integration and ergonomics report; P1 cannot start without explicit approval.

P1 adds typed golden cases, an importable evaluation-target adapter, deterministic assertions, agreement metrics, and CI-compatible exit codes. P2 adds a loopback-only FastAPI/Jinja2/HTMX application, synchronous transactional feedback, append-only overrides, and safe static export. Counterfactuals, LLM judging, drift, outcomes reconciliation, GitHub Actions wiring, OTLP runtime export, and Parquet retention remain outside the first increment.

The full data model, invariants, security requirements, acceptance tests, and non-goals are defined in the approved source specification. If this summary and that specification conflict, the approved v1.2 specification governs.
