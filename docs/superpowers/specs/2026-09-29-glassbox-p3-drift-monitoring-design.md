# P3b: Drift monitoring design

**Date:** 2026-09-29
**Status:** Proposed — revisions pending review
**Scope:** drift monitoring only. Counterfactual providers and external alerts remain deferred P3 work.

## Goal

Glassbox will identify meaningful changes in an individual agent's recorded decision behaviour and trace-level operational behaviour. A planner can inspect the current state at an authenticated local `/drift` page, while an explicit CLI command persists immutable snapshots for audit. Drift is informational in this increment: it does not notify an external system, fail CI, or block agent execution.

The source specification's P3 requirement remains the acceptance target: PSI/CUSUM monitoring must detect an injected synthetic shift. This design supplies the missing boundaries needed to make that requirement trustworthy: immutable baselines, one calculation anchor per report, agent-scoped cohorts, explicit sample units, policy provenance, and an honest insufficient-data state.

## Scope and non-goals

Included:

- a versioned TOML drift policy and checked-in default policy;
- per-`agent_name` immutable baseline profiles;
- PSI monitoring of decision confidence and decision type;
- CUSUM monitoring of trace latency and trace cost;
- an explicit CLI baseline command and persisted CLI report snapshots;
- an authenticated, read-only, live localhost report; and
- synthetic-shift, persistence, read-only, and aggregation verification.

Excluded:

- counterfactual providers and Decision Card counterfactual content;
- Discord or other external notifications;
- CI gating or non-zero exit for a detected shift;
- live dashboard charts beyond the localhost drift report;
- per-decision cost or latency attribution; and
- automatic re-baselining or scheduled monitoring.

The existing local access-token login/session middleware protects `/drift` exactly as it protects the queue, Decision Card, and trace pages. This is not a new multi-tenant identity or authorization model.

## Cohorts and sample populations

Every baseline and report is scoped by **`agent_name` only**. Glassbox must never mix unrelated agents' distributions. `agent_version` is deliberately not part of the cohort key: a prompt or model rollout is a drift event that the monitor should expose, not hide by starting a new cohort.

Both the baseline and every report retain an agent-version frequency map for provenance and investigation. The report can therefore show that a detected change coincided with a rollout without treating versions as incomparable populations.

The four signals use two explicitly different sample units:

| Signal | Population | Query membership | Algorithm | Meaning |
|---|---|---|---|---|
| `confidence` | decision | `decisions.agent_name`, `decided_at` in window | PSI | Shift in recorded confidence distribution. |
| `decision_type` | decision | `decisions.agent_name`, `decided_at` in window | PSI | Shift in the mix of decision categories. |
| `trace_latency_ms` | trace | `traces.agent_name`, `started_at` in window | CUSUM | Shift in end-to-end trace latency. |
| `trace_cost_usd` | trace | `traces.agent_name`, `started_at` in window | CUSUM | Shift in total trace cost. |

Trace latency and trace cost are never copied to every decision in a batch and are never divided by decision count. One trace can contain several decisions, so no agent-neutral per-decision attribution is sound. The UI labels them as trace-level metrics and reports decisions-per-trace summary statistics beside them. A batch-size change can therefore be recognized as operational context rather than misrepresented as a reasoning-quality regression.

`traces.latency_ms` and `traces.total_cost_usd` are independently nullable. A null value is excluded from that signal's population only; it is never coerced to zero and does not exclude the trace from the other signal or decisions-per-trace context. Each baseline/run/result stores the resulting non-null sample count for its own signal. A signal with too few non-null samples is `insufficient_data`, including when enough traces exist overall.

## Drift policy

The default checked-in policy lives at `glassbox/eval/policies/drift_v1.toml`. The CLI accepts `--policy PATH` to override it. Environment variables and dotenv are not used: this is structured, non-secret, auditable configuration.

Every policy contains:

- a human-assigned non-empty `policy_version`;
- an absolute UTC `baseline_start` and exclusive `baseline_end`;
- one positive recent-window duration;
- a minimum baseline and recent sample count for each signal;
- confidence-bin boundaries and PSI warning/alert thresholds;
- decision-type PSI smoothing and warning/alert thresholds; and
- CUSUM reference and warning/alert limits for trace latency and cost.

The shipped `drift_v1.toml` fixes these initial settings: confidence uses ten equal-width `[0, 1]` bins; both PSI signals require 100 baseline and 30 recent observations, warn at `0.10`, and alert at `0.20`; both CUSUM signals require 50 baseline and 20 recent traces, use standardized residuals with reference value `0.5`, warn at `3.0`, and alert at `5.0`. Decision types are the categories observed in the materialized baseline plus an `other` bucket; a small policy-configured positive smoothing value makes a new or absent category measurable without division by zero.

Policy loading validates every field before a database query: timestamps must be UTC and ordered, durations/counts/bins must be positive and ordered, thresholds finite and ordered, and all four required signals present exactly once. The engine computes SHA-256 over the policy's canonical bytes and retains both `policy_version` and `policy_hash` in every baseline and snapshot. A version or content change is never silently comparable to a previous policy.

## Windows and immutable baselines

The baseline interval is the policy's fixed historical half-open window `[baseline_start, baseline_end)`. It is materialized rather than re-queried for every report. Materialization records the exact PSI distributions, CUSUM reference statistics, sample counts, version maps, and decisions-per-trace summaries needed to compare later data. Consequently, trace archival or database retention cannot silently alter a previously established reference distribution.

Each report captures one UTC `as_of` instant at its entry point. Its recent half-open window is `[as_of - recent_duration, as_of)`. That exact `as_of`, recent bounds, and policy hash are carried through the entire engine invocation and persisted for a CLI snapshot. A CLI snapshot and a live refresh may legitimately differ when they use different `as_of` instants; neither computes "now" more than once internally.

The baseline command is:

```text
glassbox drift baseline --agent NAME [--policy PATH] [--supersede-baseline]
```

It opens a write-capable database only after validating the policy and agent name. It refuses if a current baseline already exists for `(agent_name, policy_hash)`. `--supersede-baseline` is the sole explicit replacement path: it appends a new immutable baseline profile linked to the former profile through `supersedes_baseline_id`; it never updates or deletes the old profile. Head discovery and insertion occur in one immediate write transaction so concurrent baseline creation cannot produce ambiguous active profiles.

`glassbox drift baseline` exits `0` only after creating a baseline. Malformed policy, unavailable database, invalid agent/policy input, insufficient baseline samples, or an existing baseline without `--supersede-baseline` are operational failures and exit `2`.

An ordinary persisted report is produced by:

```text
glassbox drift --agent NAME [--policy PATH]
```

It selects the active baseline head for the requested agent and exact policy hash, computes once at its captured `as_of`, writes an immutable report snapshot, and emits a machine-readable JSON report. All completed drift states, including `drift_detected` and `insufficient_data`, exit `0`. Malformed policy, unavailable database, invalid report-command input, or a persistence failure exit `2`, matching the established operational-error convention. Detected drift is intentionally not a CLI failure in this increment.

## Status and aggregation

Each signal has `healthy`, `watch`, `drift_detected`, or `insufficient_data` status. PSI is compared to its configured thresholds. CUSUM uses the baseline mean and standard deviation to standardize recent trace measurements, resets the one-sided positive and negative sums at zero, and reports the larger absolute cumulative excursion against its configured warning and alert limits. A baseline standard deviation of zero is insufficient data rather than an invented infinite signal.

Overall status is evaluated in this fixed order:

1. If any required signal is insufficient, report `insufficient_data`.
2. Otherwise, if any signal is `drift_detected`, report `drift_detected`.
3. Otherwise, if any signal is `watch`, report `watch`.
4. Otherwise, report `healthy`.

The report distinguishes decision-behaviour and trace-operational signals so an overall shift never implies that every kind of metric moved. It always identifies the highest-severity contributing signal(s).

`insufficient_data` carries one actionable reason, never a generic blank state:

| Reason | Meaning and operator action |
|---|---|
| `baseline_not_created` | No baseline exists for this agent. Run `glassbox drift baseline`. |
| `policy_changed_requires_rebaseline` | A baseline exists for the agent, but not this policy hash. Materialize a baseline for the changed policy. |
| `baseline_window_too_small` | The policy's historical interval lacks a signal's minimum sample count. Wait for/choose an adequate historical window, then create the baseline. |
| `recent_window_too_small` | A valid baseline exists but the current recent window lacks enough samples. Wait for more recent activity. |

The baseline command refuses to create a profile with insufficient baseline data and names the deficient signal(s). The live report can still identify that condition by querying the policy's baseline interval without materializing or writing anything.

## Persistence

Drift records use dedicated storage rather than overloading `eval_results.passed`: evaluation rows cannot truthfully represent the four-valued `watch`/`insufficient_data` status or typed drift details. The migration adds:

- `drift_baselines`: immutable baseline identity, agent name, policy version/hash, fixed baseline bounds, creation time, sample counts, agent-version maps, serialized reference statistics, and optional predecessor ID;
- `drift_runs`: immutable CLI snapshot identity, exact baseline ID, agent name, policy version/hash, captured `as_of`, recent bounds, overall status/reason, counts, version maps, and decisions-per-trace summary; and
- `drift_results`: one typed signal result per drift run: signal name, population, algorithm, status, baseline/recent counts, metric value, warning/alert thresholds, and JSON-safe diagnostic details.

All IDs are system-generated ULIDs. Foreign keys preserve audit history: a run references its baseline with `ON DELETE RESTRICT`; a baseline references its predecessor with `ON DELETE RESTRICT`; results cascade only when their parent run is explicitly deleted. The repository owns baseline head classification, immutable inserts, and all drift query SQL. The strict schema, current-schema fingerprint, and `schema.sql` continue to be mirrored and tested exactly as in P0--P3a.

## Live report

`GET /drift` is protected by the existing local session middleware. Its landing page lists every distinct `agent_name` observed in either `decisions` or `traces`, whether or not that agent has a baseline. It links each to `GET /drift?agent=NAME`, making `baseline_not_created` directly discoverable. With an explicit agent, it loads the default checked-in policy, opens the database only with `Database.open_read_only()`, locates the matching active baseline, captures one `as_of`, and runs the shared engine without persistence.

The overview-first page shows:

- status, calculated-at time, and concise action text;
- agent cohort and baseline/recent agent-version mixes;
- policy version/hash, baseline ID, and time ranges;
- decision and trace sample counts plus decisions-per-trace context;
- four signal cards with population, algorithm, current metric, thresholds, and status; and
- a detail table/chart for the selected signal's baseline versus recent distribution or CUSUM path.

No browser query parameter becomes SQL text or a policy path. `agent` is a bound query value validated against known cohorts; the policy path is never operator-controlled through HTTP. A missing agent returns the established generic not-found page. `ReadOnlyDatabaseError` and `sqlite3.Error` reuse the server's existing `unavailable(request)` helper, preserving generic error text and the established 503 path.

The live page does not write a drift run, baseline, or other database state. It labels itself "calculated now" so it cannot be mistaken for an auditable CLI snapshot.

## Architecture boundaries

P3b deliberately keeps its policy parser, typed report models, metrics, and calculation engine in one public module: `glassbox.eval.drift`. There are no `drift_*` sibling modules for the web layer to import accidentally. That module may import `glassbox.store`; it never imports `sdk`, `collector`, or `web`. The repository is the only SQLite-aware layer. The web layer imports only this public drift entry point, supplies a read-only repository/connection, and converts typed report models to display models before Jinja rendering. The CLI imports `glassbox.eval.drift` only in its `drift` branch, so tracing, serving, export, deterministic evaluation, and the judge command do not load drift monitoring code.

Because `glassbox.eval.drift` is the only drift module, there is no unlisted sibling for an import-linter rule to miss. The web architecture test explicitly permits its direct `glassbox.eval.drift` dependency and rejects any other `glassbox.eval.drift_*` or `glassbox.eval.drift.` import if one is introduced later. The existing `sdk-dependencies` and `collector-dependencies` contracts already forbid all of `glassbox.eval`, which includes `glassbox.eval.drift`; architecture tests continue to assert that all named contracts are registered and enforced.

## Verification

- Policy tests reject malformed or ambiguous TOML before database access; they prove canonical policy hashing and version/hash isolation.
- Metric tests cover confidence bins, decision-type `other` handling, PSI smoothing, CUSUM positive/negative shifts, zero-variance baselines, every threshold boundary, and every per-signal/overall status branch.
- Repository tests prove agent-name-only cohort selection, separate decision/trace sample populations, no duplicated trace cost/latency for batch decisions, stable active-baseline head selection, refusal by default, atomic supersession, and immutable run-to-baseline provenance.
- A synthetic confidence, decision-type, latency, and cost shift creates `drift_detected`; an injected batch-size-only change is visible through decisions-per-trace context and remains labelled trace-operational rather than decision-behaviour drift.
- CLI tests prove `as_of` is captured once, baseline and snapshot output preserves exact windows/provenance, detected drift exits `0`, and operational/policy failures exit `2`.
- Web tests prove the existing session guard protects `/drift`, missing/unknown cohorts are generic, live calculations use read-only mode and create no rows or database files, each insufficient-data reason renders actionable guidance, and database errors reuse the existing generic unavailable response.
- Architecture tests prove the import boundaries and lazy CLI import path. Full pytest, Ruff, mypy, and import-linter must pass.

## Deferred work

Counterfactual providers remain a separate P3 design because they require a side-effect-free agent entry point outside Glassbox's ownership. Discord alert delivery, automatic scheduling, notification routing, CI gating, and generated P5 dashboard artifacts are also deferred. The source P3 acceptance test for an injected synthetic shift is satisfied by this increment without any external egress or notification dependency.
