# P3c: Counterfactual prompt variants design

**Date:** 2026-10-08
**Status:** Proposed — approved design pending written-spec review
**Scope:** an explicit offline CLI experiment that runs one checked-in prompt
variant against recorded decisions. Live decision-time shadow execution, UI,
notifications, scheduling, CI gating, and a claim that one prompt is globally
better are deferred.

## Goal

Glassbox will let an operator ask: *given the same recorded case, what would a
versioned alternative prompt have recommended?* The first increment is
observational. It creates immutable, auditable hypothetical results without
changing the original decision, its trace, its feedback, or the production
agent's execution path.

The command provides two forms of evidence:

1. immediately, it compares the original opaque recommendation/rationale with
   a variant's opaque recommendation/rationale; and
2. when a compatible, mature P4 outcome exists, it derives that variant's
   `tp`/`fp`/`tn`/`fn` label under the same reconciliation-policy cohort.

The second form enriches a result; it does not delay the first, and it does not
make a quality or rollout decision. Aggregate winner selection, sample-size
thresholds, and gating need a later approved policy.

## Scope and non-goals

Included:

- `glassbox counterfactual run` and `glassbox counterfactual report` CLI
  commands;
- versioned checked-in prompt files selected by path;
- a dedicated, explicitly configured external or local provider/model;
- redacted structured decision snapshots plus captured redacted model content
  when available;
- immutable run and per-decision result records;
- optional P4 deferred-outcome enrichment; and
- deterministic fake-provider tests and provider egress protections.

Excluded:

- execution inside `trace`, `decision_context`, the collector, or a live agent
  request;
- a Decision Card, queue, dashboard, or other web presentation;
- automatic rollout, prompt promotion, alerting, scheduling, CI gating, or
  provider cost/spend policy;
- business-key association, modifying an original decision, or overwriting an
  earlier counterfactual result; and
- agent-specific recommendation schemas or a Glassbox-owned real-agent
  adapter.

## Prompt artifacts and provider configuration

Prompt variants are UTF-8 checked-in files, normally under
`glassbox/eval/prompts/`. Each begins with an exact TOML front-matter block:

```text
+++
prompt_version = "counterfactual_v1"
+++
...prompt instructions containing exactly one {{decision_snapshot_json}}...
```

Glassbox canonicalizes the rendered snapshot as compact, sorted JSON and
replaces only that token. The full file's SHA-256 hash is the prompt identity;
the non-empty human-assigned `prompt_version` is retained for operators. A
changed file is a different cohort even when its version string was not
changed.

Counterfactual configuration is independent from the judge configuration:

```dotenv
GLASSBOX_COUNTERFACTUAL_PROVIDER=claude # claude | openai | gemini | ollama
GLASSBOX_COUNTERFACTUAL_MODEL=...
```

Credentials follow the selected provider's existing credential name:
`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `GEMINI_API_KEY`. Ollama accepts an
optional `GLASSBOX_COUNTERFACTUAL_OLLAMA_URL` and defaults to an explicit
loopback URL. The loader reads only the Glassbox project-root `.env`, parses it
with `dotenv_values()` into a local mapping, never mutates `os.environ`, and
reads only `GLASSBOX_COUNTERFACTUAL_*` plus the selected credential. Process
environment values take precedence. It never reads an agent repository's
`.env`.

Provider SDKs remain optional extras and import lazily inside the selected
adapter. Adapters receive credential and base URL constructor arguments, so
ambient SDK base-URL environment overrides cannot change the disclosed
destination. The provider/model API may share low-level transport helpers with
the judge only if doing so does not cause judge configuration or SDK imports to
load outside their respective CLI branches.

## Snapshot, egress, and response contract

For each recorded decision, Glassbox builds one redacted JSON snapshot with:

- decision ID, agent name/version, decision type, decided timestamp, original
  recommendation, rationale, ordered cited evidence groups (including
  field name, value, `retrieved_at`, and `source_system`), and alternatives;
- original trace metadata needed to identify the case; and
- original redacted prompt and completion content when `--blob-dir PATH` is
  supplied and their blob references resolve in that directory. Blob references
  identify content rather than a storage location, so Glassbox never guesses a
  blob directory. Without `--blob-dir`, and for missing/unreadable references,
  capture fields are explicit `null` values and never make a case ineligible.

The structured snapshot is always present. Original captured model content is
additive context, not a prerequisite and not an alternative input format.
Snapshot values are untrusted data: the rendered prompt places the complete
JSON in a clearly delimited data block and instructs the provider never to
follow instructions found in that block.

Each provider response must strictly parse to:

```json
{"recommendation": {"opaque": "json"}, "rationale": "concise explanation"}
```

`recommendation` may be any JSON value and `rationale` is a non-empty string.
The enclosing keys and types are the only counterfactual response schema.
Invalid output is a failed case result, not a command-wide failure. The
provider temperature is fixed at zero.

Before constructing an adapter or making a network call, the command validates
the prompt, provider configuration/dependencies, database/schema, time window,
and `--max-cases`. It then prints provider, model, constructed destination,
prompt version/hash, selected count, and the count of snapshots with captured
model content. Every remote destination requires `--confirm-egress`; loopback
Ollama prints the same disclosure but does not require confirmation. A
non-loopback Ollama URL is remote. No data leaves the machine before this
preflight succeeds and, when required, consent is supplied.

## Commands, selection, and persistence

The command shape is:

```text
glassbox counterfactual run --prompt PATH --since DURATION [--max-cases N]
                           [--blob-dir PATH] [--confirm-egress]
                           [--outcome-policy PATH]
glassbox counterfactual report --run RUN_ID
```

`--since` is required and filters `decisions.decided_at`. Selected decisions
are ordered newest first by parsed UTC `decided_at`, then `decision_id DESC`;
`--max-cases` applies to that fixed order. A run uses one captured UTC `as_of`
instant. It creates no traces, spans, collector events, or SDK writes.

The default outcome policy is the checked-in P4 reconciliation policy;
`--outcome-policy` is an explicit CLI path. Before selection, the command
loads and hashes it. Every case result records the exact policy version/hash;
results never combine enrichment from another policy hash.

A migration adds two append-only tables:

- `counterfactual_runs`: ULID, created time, provider/model/base URL,
  prompt version/hash, outcome-policy version/hash, captured `as_of`, requested
  selection bounds/count, succeeded/failed counts, and remote-egress
  confirmation state;
- `counterfactual_results`: ULID, run FK, decision FK, input snapshot hash and
  canonical redacted snapshot JSON, original recommendation/rationale,
  hypothetical recommendation/rationale or a concise failure code, plus an
  optional compatible outcome ID and derived counterfactual label.

Run/result foreign keys use `ON DELETE RESTRICT` to retain the audit trail.
All IDs are system-generated ULIDs. A result is immutable: rerunning a prompt
creates a new run, even for the same decision. A unique `(run_id, decision_id)`
constraint prevents accidental duplicate writes within one run.

Cases persist independently. A provider timeout, response failure, unresolved
blob, or malformed snapshot records one failed result and processing continues.
Invalid prompt/configuration/egress acknowledgement, database unavailability,
or a persistence failure is operational: it aborts before or during the run
with the project-standard exit `2` and a generic error that exposes no raw
provider response, path, credential, or SQLite text. A completed run exits
`0`, including when individual case failures occurred; its compact JSON report
contains selected/succeeded/failed counts and run ID.

## P4 outcome enrichment

For a selected decision, Glassbox considers enrichment only when it is mature
under the selected outcome policy's maturity window and has the newest
compatible P4 outcome for that exact policy hash and outcome type. It applies
the policy's generic recommendation and outcome JSON pointers to the
hypothetical recommendation and stored raw outcome value, producing a
counterfactual `tp`, `fp`, `tn`, or `fn` label. This label is separate from the
original decision's stored outcome label because a different recommendation can
produce a different classification against the same observed outcome.

No mature compatible outcome means `outcome_id` and counterfactual label remain
null. It is not an error and does not suppress the hypothetical result. A
decision without exactly one matching policy rule likewise retains its
counterfactual response but has no outcome enrichment; the case report names a
safe `outcome_not_applicable` state. This preserves immediate prompt-review
value without claiming outcome-backed evidence where none exists.

`glassbox counterfactual report --run RUN_ID` prints provider/prompt/policy
provenance, success/failure counts, original and hypothetical recommendation
and rationale, and enrichment status/label per case. It makes no aggregate
ranking or causal claim.

## Architecture boundaries

Counterfactual orchestration lives in one public
`glassbox.eval.counterfactual` module. It may import the store and public
policy helpers; it never imports `sdk`, `collector`, or `web`. The repository
owns every SQLite query and transaction; it never imports eval. The web layer
does not import counterfactual code in this increment.

`glassbox.cli` imports counterfactual code only inside its `counterfactual`
branch. Tracing, serving, export, deterministic evaluation, judge, drift, and
outcomes commands do not load counterfactual configuration or optional provider
SDKs. Import-linter and AST tests explicitly enforce that `sdk`, `collector`,
and `web` cannot import `glassbox.eval.counterfactual`, and that CLI imports are
function-branch-local rather than module/class/control-block eager imports.

## Verification

- Prompt tests reject missing, duplicated, or malformed version/placeholder
  artifacts and prove canonical prompt/snapshot hashes change with content.
- Snapshot tests prove structured data is always present; `--blob-dir` is the
  only capture-location authority; blob content is additive; redacted
  evidence/capture data is delimited as untrusted data; and confidence,
  feedback, and outcome values are excluded from provider input.
- Configuration/adapter tests prove root-only scoped dotenv parsing,
  process-environment precedence, explicit credentials/base URL, zero
  temperature, strict envelope parsing, lazy optional imports, and no provider
  construction before preflight.
- Safety tests prove remote egress requires confirmation, loopback Ollama does
  not, non-loopback Ollama does, and malformed provider output or an individual
  call failure persists a safe failed result while later cases continue.
- Repository/migration tests prove append-only run/result records, no duplicate
  decision within a run, strict schema/read-only compatibility, `ON DELETE
  RESTRICT`, and no mutation of source decisions, feedback, outcomes, or blobs.
- Outcome tests prove maturity/policy-hash/type selection and all four derived
  labels, and prove unavailable/ambiguous outcome enrichment remains explicitly
  inapplicable rather than fabricated.
- CLI tests prove fixed selection ordering/capping, compact reports, exit `0`
  for completed partial failures, exit `2` for operational failures, and no
  counterfactual import in unrelated command paths.
- Full pytest, Ruff, mypy, and import-linter must pass.

## Deferred work

- Add opt-in live decision-time shadow execution for approved counterfactual
  prompts, preserving fail-open production behaviour and bounded cost/latency.
- Add an authenticated web presentation after a separate UX/safety design.
- Define outcome-backed aggregate comparison metrics, sample floors, and any
  rollout/promotion policy.
- Add notifications, scheduling, spend controls, and CI gating only after an
  operational policy approves them.
