# Glassbox P1 Live-Provider Smoke Design

**Status:** Approved 2026-09-15

## Purpose

Add a deliberately non-blocking, bounded live-provider smoke run for the
replenishment triage agent. It complements, but never replaces, P1's offline
scripted-provider evaluation gate.

## Scope and ownership

The independently versioned replenishment-agent repository owns the smoke
script, fixed case profile, provider configuration, credentials, and tests.
Glassbox continues to own only dependency-neutral evaluation contracts and
deterministic evaluation infrastructure. Glassbox does not add a provider SDK,
credential loader, secret file handling, or CI workflow.

The smoke script uses the agent's existing provider and model configuration:
`AGENT_PROVIDER`, `AGENT_MODEL`, and the selected provider's existing
credential environment variable. It validates those settings with the agent's
current configuration validation before any case is run.

## Execution model

The agent-owned script evolves from a one-case probe to a committed profile of
five existing replenishment golden cases: one routine, one ambiguous, one
adversarial, and two cases that include a do-nothing decision. The profile is
an explicit, reviewable list of case identifiers/paths; it cannot expand to
the full 40-case suite implicitly.

The command refuses to start unless `LIVE_EVAL_ENABLED=1` is present. It runs
the five cases sequentially through the real `TriageAgent` and active provider,
using the same `GoldenCase` input and `DecisionResult` output contract as the
scripted adapter. Each invocation isolates its trace/collector data in a
temporary database. It emits one canonical JSON report containing selected case
IDs, per-case assertion outcomes and errors, plus aggregate latency and token
measurements. The current provider response interface has no billed-cost field,
so both cost totals are `null` (unavailable), never a misleading zero.

## Failure and exit behavior

Missing opt-in, invalid provider settings, or unavailable credentials fail
before any provider call. A provider, parsing, trace, or assertion failure for
one case becomes that case's reported failure and does not stop subsequent
cases. Reports preserve only an error's exception type, not provider exception
text that could contain request or credential details. The command exits nonzero if any selected case errors or fails a
deterministic structural assertion.

The live report never supplies a CI gate, changes `glassbox eval`, or modifies
the deterministic manifest's exit behavior. Its metrics are observational;
they do not enforce a cost or quality threshold until a separate policy is
approved.

## Verification

Agent-repository tests prove that the script:

1. refuses unless `LIVE_EVAL_ENABLED=1`, before loading provider
   configuration;
2. selects exactly the committed five-case profile;
3. reports exactly one result per selected case in canonical JSON;
4. continues after an individual target failure; and
5. reports aggregate live execution measurements and `null` cost values when
   provider-billed cost is unavailable.

Glassbox's existing deterministic CLI and CI tests remain unchanged and never
invoke the live script. The agent README documents the explicit opt-in command
and states that cost/token figures are observational rather than enforcement
thresholds.
