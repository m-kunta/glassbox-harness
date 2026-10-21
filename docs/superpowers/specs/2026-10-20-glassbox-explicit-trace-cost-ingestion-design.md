# Explicit Trace Cost Ingestion Design

**Status:** Approved design — pending written-spec review

## Purpose

Glassbox already persists `traces.total_cost_usd` and treats `NULL` as an unknown
observation in drift reporting. It does not yet expose an SDK contract for an
instrumented agent to supply a known cost. This increment adds that narrow,
provider-neutral handoff. It does not estimate prices, enable cost monitoring, or
change any drift policy.

## Public contract

Glassbox exposes `set_trace_cost_usd(total_cost_usd: float) -> None` from both
`glassbox` and `glassbox.sdk`.

The argument is the complete USD cost for the *current root trace*, not a
per-call increment. An integration that makes several provider calls sums its
known charges before supplying the final total. A later valid call replaces an
earlier value, allowing an integration to update a provisional total after its
last provider response.

The function accepts only finite, non-negative numeric values. An invalid value,
an absent active trace, or disabled instrumentation is a silent no-op. This
preserves Glassbox's existing fail-open guarantee: instrumentation must never
alter an agent's returned value or exception behavior. `0.0` is written only
when the integration explicitly supplies it; Glassbox never manufactures a zero
for an unknown cost.

## Data flow

`TraceState` gains an optional `total_cost_usd` field. A successful public call
replaces the active context-local trace state with that field populated. Root
trace completion reads that state and includes `total_cost_usd` on the terminal
`TraceEvent` only when the caller supplied it.

The existing repository upsert already distinguishes event fields explicitly
supplied by the event model from absent optional fields. Therefore a terminal
event with cost updates the existing trace row, while traces with no cost call
remain `NULL`. No migration or new persistence API is required.

The value is intentionally trace-level. Glassbox does not allocate it among
decisions or spans, because one trace may contain multiple decisions and an
allocation would create a false per-decision metric.

## Scope boundaries

- No provider SDKs, pricing tables, token-to-cost estimation, or credentials are
  added.
- No automatic propagation from span attributes is inferred.
- The shipped drift policy continues to omit `trace_cost_usd`; an operator must
  deliberately enable it in a future policy revision and establish a new,
  policy-hash-specific baseline.
- This change does not modify latency collection, which already has an
  independent completion-time path.

## Verification

Tests cover:

1. public re-export from `glassbox` and `glassbox.sdk`;
2. a cost supplied inside a trace appearing only on its completion event and in
   the persisted trace row;
3. replacement semantics for two valid calls in one trace;
4. no active trace, disabled tracing, negative, non-finite, and non-numeric
   inputs producing no event changes and no agent-visible exception; and
5. omission preserving `total_cost_usd IS NULL`, including a zero value written
   only after an explicit call.

Existing SDK, repository, type, lint, and import-boundary checks remain part of
the verification gate.
