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
assertions, metrics, gates, and CLI reporting. The independently versioned
replenishment-agent repository owns `integrations/replenishment_triage.py`,
which translates between those contracts and its own input and result classes.
It also owns its `goldens/replenishment_triage/` cases and suite manifest, so
the adapter, fixtures, and evaluation policy resolve from one project root.

The adapter invokes the real `TriageAgent.run` orchestration path. P1 supplies
a scripted LLM provider so this execution is repeatable, offline, and free of
provider credentials or cost. A live-provider smoke mode is deliberately
deferred: it must reuse the same target and golden cases, and must remain
outside the deterministic CI gate until its operational policy is approved.

The replenishment agent declares Glassbox in an evaluation-only dependency
extra. Its production dependency set keeps Glassbox optional, including the
existing no-op import fallback when Glassbox is absent. An evaluation command
runs in the agent environment, where both `integrations.replenishment_triage`
and the installed `glassbox` CLI are importable; Glassbox never appends a
sibling checkout to `sys.path`.

The agent expands its existing optional Glassbox import block to include
`decision_context` and `evidence` as well as `trace`. Its `ImportError`
fallback provides no-op equivalents for all three public calls, preserving the
already-tested behavior of production runs without Glassbox installed.

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
replenishment agent's types or create tracing infrastructure. Each
agent-owned target creates an isolated trace/collector context for its case,
flushes it, and returns the persisted decision data as a `DecisionResult`. A
target exception becomes a failed `DecisionResult`, rather than aborting the
suite. Glassbox verifies this generic path with fake targets; the
replenishment agent verifies its own adapter against its real `TriageAgent`.

`pyproject.toml` adds an import-linter `eval-dependencies` contract, covered
by `tests/test_architecture.py` and import-linter's real API regression test.
`glassbox.eval` may import only the dependency-neutral events contracts and
the read-only store boundary; it must not import `collector`, `sdk`, `explain`,
or `web`. The existing `store-dependencies` contract continues to forbid the
reverse `store → eval` edge.

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

These checks apply uniformly to every manifest category, including do-nothing
cases. A no-action decision must show the evidence it weighed and an action it
rejected. If P1.3 case authoring finds the floor causes invented evidence or
token alternatives, strengthen the real agent's reasoning requirements; do not
create a category exemption.

It compares predicted and expected urgency on the ordered SLA scale
`LOW < MEDIUM < HIGH < CRITICAL`, reporting **linear weighted kappa** and an
urgency confusion matrix. Adjacent-level errors retain material weight because
each tier changes the planner response window. The source-specification gate
remains `urgency_agreement >= 0.6`; P1.3 must record the completed 40-case
linear-kappa value before any threshold change. Operational reporting includes
deterministic pass rate, p50/p95 latency, cost per decision, token efficiency,
and error rate.

The CLI emits a per-case and per-assertion breakdown and exits non-zero if any
manifest gate fails. The first implementation supplies a small representative
executable seed. The 40-case balanced suite is a later P1.3 artifact with the
specified routine, ambiguous, adversarial, and do-nothing distribution.

## File layout

```text
glassbox/
  eval/
    models.py       # contracts and suite/result records
    target.py       # EvaluationTarget and safe target loader
    assertions.py   # deterministic checks
    metrics.py      # urgency agreement and operational metrics
    runner.py       # isolated execution and gate evaluation
  cli.py          # `glassbox eval` command
tests/eval/       # generic contract, runner, assertion, metric, and CLI tests

AI-driven-replenishment-exception-triage-agent/
  integrations/replenishment_triage.py  # real-agent translation adapter
  goldens/replenishment_triage/
    cases/        # readable one-exception case records
    manifest.yaml # target, checks, and gates
  tests/test_replenishment_evaluation.py # scripted-provider integration test
```

## Verification

Glassbox tests cover contract validation, target loading, each deterministic
assertion, exception-to-failure conversion, weighted-kappa edge cases, gate
exit behavior, generic fake targets, and the import-linter `eval-dependencies`
contract. The replenishment-agent repository tests real-agent execution through
the scripted provider, including persisted decision/evidence telemetry and the
expanded no-Glassbox production fallback. Glassbox tests also prove the runner
itself never imports replenishment-specific types.

The README gains one short `glassbox eval` example. `TODO.md` records the P1
approval and the deferred live-provider smoke mode.
