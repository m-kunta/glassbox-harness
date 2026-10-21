# Live Counterfactual Shadow Execution Design

**Status:** Approved design — pending written-spec review

## Purpose

Glassbox can run approved counterfactual prompts offline against stored
decisions. This increment adds an explicitly opt-in, bounded background path
for an instrumented agent to submit a just-completed production decision for
the same counterfactual evaluation. It is an observation mechanism: it must
not modify the production decision, rationale, returned value, latency, or
control flow.

It does not add scheduled execution, automatic promotion, dashboard alerts,
or a new evaluator. It reuses the existing counterfactual prompt artifacts,
provider adapters, parser, immutable run/result records, and fact-only prompt
contract.

## Opt-in and egress policy

Live shadowing is disabled unless `GLASSBOX_SHADOW_ENABLED=1` is set before
the agent starts. It is per-trace and per-artifact: after a production decision
has been finalized, the owning agent explicitly submits that decision ID and
the identity of an approved counterfactual artifact. There is no global
sampling or implicit selection of prompt artifacts.

Remote provider execution additionally requires
`GLASSBOX_SHADOW_ALLOW_EGRESS=1`. A literal-loopback Ollama destination is
local and requires only `GLASSBOX_SHADOW_ENABLED=1`. Configuration and prompt
artifact validation occur before a job becomes provider-eligible. Invalid,
disabled, or unauthorized requests are inert no-ops from the production
agent's perspective.

The integration lives in a dedicated `glassbox.eval.shadow` boundary, not in
the SDK. This preserves the existing `sdk -> eval` prohibition: an agent that
chooses this feature imports the opt-in integration directly after its normal
decision path. `sdk`, `collector`, and `web` remain forbidden from importing
the shadow module.

## Bounded background execution

An accepted request contains only a decision identifier and approved artifact
identity. Production data remains immutable and is reconstructed from the
persisted decision by the worker; the original recommendation, rationale,
confidence, feedback, and outcome labels are never sent to the provider.

The process owns a daemon worker with one active provider call and one queued
job. A full queue, a ten-job process-lifetime cap, disabled configuration, or
any worker failure drops the request immediately. The caller receives no
result and never waits for provider, collector, or database work.

Before it can contact a provider, a worker polls for at most five seconds for
the collector to persist the source decision. This handles Glassbox's
fail-open asynchronous collection without weakening the existing foreign-key
audit guarantee. If the decision is still absent, the worker makes no provider
call and increments a local `source_decision_unavailable` diagnostic counter.

Once the source decision exists, the worker has a ten-second provider-call
deadline. It uses the existing counterfactual configuration, provider adapter,
prompt renderer, strict parser, and redacted fact-only snapshot builder.
Provider timeouts, provider errors, parser failures, and write failures are
contained by the worker and recorded as local diagnostic counters. They never
alter production behavior.

## Immutable audit records

No new orphan-attempt table is introduced. A provider call is made only after
the source decision exists, so normal `counterfactual_runs` and
`counterfactual_results` records can retain their current foreign-key and
append-only guarantees.

`counterfactual_runs` gains an `execution_mode` with the values `offline` and
`live_shadow`. Existing rows migrate as `offline`. Each successful persistence
attempt for a live job creates one `live_shadow` run and its one result, with
the existing provider, model, destination, prompt hash, and outcome-policy
provenance fields filled in exactly as for an offline execution. Reporting can
therefore keep offline experiments and production shadows distinct.

Live jobs do not attempt outcome enrichment at decision time. Their result is
persisted with the existing not-yet-applicable outcome state; a later reporting
feature may join mature outcomes deliberately.

Queue/cap drops and jobs whose source decision never arrives are deliberately
not SQLite records: a durable counterfactual result without its required source
decision would be false audit history. The worker exposes those conditions as
in-process diagnostics instead.

## Failure isolation and verification

Tests prove that the production output is identical when shadowing is disabled,
unauthorized, queued, successful, or failing. They also cover:

1. no provider call before the collector-persisted decision is observable;
2. one immutable `live_shadow` run/result for a persisted source decision;
3. fact-only prompt contents excluding original recommendation, rationale,
   confidence, feedback, and outcomes;
4. immediate return on queue saturation and lifetime-cap exhaustion;
5. remote-egress rejection, source-decision expiry, provider timeout, parser
   failure, and storage failure remaining contained and counted; and
6. migration and report behavior keeping historical offline records separate
   from live-shadow records.

The full test suite, type checking, linting, and import-linter checks remain
required. The import contract explicitly names each new shadow module so a
future sibling module cannot bypass the isolation boundary.
