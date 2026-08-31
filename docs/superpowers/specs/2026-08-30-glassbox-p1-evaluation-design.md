# Glassbox P1 Evaluation Design

**Status:** Approved 2026-08-30

## Purpose

P1 evaluates agent decisions deterministically. It must exercise the real
replenishment-triage agent while keeping the evaluation engine understandable
and reusable for an agent that has no knowledge of replenishment.

The approved source specification remains
[`docs/spec/glassbox-spec-v1.2.md`](../../spec/glassbox-spec-v1.2.md). This
document resolves P1 implementation choices that the source specification
leaves open.

## Scope and boundaries

Glassbox owns generic evaluation contracts, suite loading, deterministic
assertions, metrics, gates, and CLI reporting. The repository-level
`integrations/replenishment_triage.py` adapter owns translation between those
contracts and the external replenishment agent's input and result classes.

The adapter invokes the real `TriageAgent.run` orchestration path. P1 supplies
a scripted LLM provider so this execution is repeatable, offline, and free of
provider credentials or cost. A live-provider smoke mode is deliberately
deferred: it must reuse the same target and golden cases, and must remain
outside the deterministic CI gate until its operational policy is approved.

P1 also adds minimal `decision_context` and `evidence` calls at the real-agent
boundary. The adapter reads the resulting persisted decision, evidence,
citations, and alternatives; it does not manufacture an evaluation-only shadow
record. A follow-up revisits the domain semantics of that mapping after the
seed suite establishes end-to-end behavior.

## Contracts

`glassbox.eval` defines these dependency-neutral types:

- `GoldenCase`: stable `case_id`, typed JSON-like input payload, expected
  labels, and descriptive metadata.
- `DecisionResult`: structured decision(s), evidence, citations, operational
  measurements, and an optional captured execution error.
- `EvaluationTarget`: protocol with
  `run_case(case: GoldenCase) -> DecisionResult`.
- Suite and result records: manifest-driven target import path, golden-set
  location, assertions, metrics, gate thresholds, and reported outcomes.

The runner imports a target by manifest path. It does not import the
replenishment agent's types. A target exception becomes a failed
`DecisionResult`, rather than aborting the suite.

## Case and adapter model

Each replenishment golden case represents **one exception**. Its fixture is a
plain, readable record with the exception input, expected urgency label, and
metadata describing the case category. The adapter maps that record to the
agent's exception schema, constructs the real agent with a scripted provider,
calls `run`, and maps its persisted Glassbox decision/evidence data back to a
generic `DecisionResult`. The real-agent integration maps the agent's
recommendation, rationale, evidence identifiers, and alternatives explicitly;
the adapter only translates those already-recorded values.

One-case execution preserves an easy-to-audit relationship between a case's
input, label, evidence, alternatives, and recommendation. The agent may batch
internally; that implementation detail never crosses the evaluation boundary.

## Execution and evaluation

For every manifest case, the runner creates an isolated Glassbox trace context,
invokes the target, captures an exception as data, and evaluates the result.
P1 includes four blocking deterministic checks:

1. recommendation satisfies its JSON schema;
2. every rationale citation resolves to evidence owned by that decision;
3. the decision has at least three evidence items; and
4. the decision has at least one considered alternative.

It also compares predicted and expected urgency, reporting weighted kappa and
an urgency confusion matrix. Operational reporting includes deterministic pass
rate, p50/p95 latency, cost per decision, token efficiency, and error rate.

The CLI emits a per-case and per-assertion breakdown and exits non-zero if any
manifest gate fails. The first implementation supplies a small representative
executable seed. The 40-case balanced suite is a later P1.3 artifact with the
specified routine, ambiguous, adversarial, and do-nothing distribution.

## File layout

```text
glassbox/eval/
  models.py       # contracts and suite/result records
  target.py       # EvaluationTarget and safe target loader
  assertions.py   # deterministic checks
  metrics.py      # urgency agreement and operational metrics
  runner.py       # isolated execution and gate evaluation
integrations/
  replenishment_triage.py  # real-agent translation adapter
goldens/replenishment_triage/
  cases/          # readable case records
  manifest.yaml   # target, checks, and gates
tests/eval/
tests/integrations/
```

## Verification

Tests cover contract validation, target loading, each deterministic assertion,
exception-to-failure conversion, weighted-kappa edge cases, gate exit behavior,
and real-agent execution through the scripted provider, including persisted
decision/evidence telemetry. They also prove the runner itself never imports
replenishment-specific types.

The README gains one short `glassbox eval` example. `TODO.md` records the P1
approval and the deferred live-provider smoke mode.
