# Glassbox P4 Outcome Reconciliation Design

**Status:** Proposed for review

**Goal:** Ingest realized, decision-linked outcomes from a JSONL export and
produce reproducible deferred-truth coverage, confusion-matrix, and override
rate reports without introducing agent-specific code into Glassbox.

## Scope

P4 adds a CLI JSONL importer and CLI reconciliation report. It does not add a
web dashboard, notification delivery, scheduling, CI enforcement, business-key
matching, or a second ingestion API. The `outcomes` table already exists from
P0; P4 makes it safe and useful to populate.

## Input contract

The importer accepts UTF-8 JSONL. Each non-blank line must be one object with:

```json
{
  "source_id": "erp-stockout-88421",
  "decision_id": "01ARZ3NDEKTSV4RRFFQ69G5FAX",
  "outcome_type": "stockout_occurred",
  "observed_at": "2026-10-05T14:00:00Z",
  "horizon_days": 30,
  "value": {"occurred": true}
}
```

`source_id` is a non-empty source-owned event identifier. `decision_id` must
already exist in the selected Glassbox database. `outcome_type` is a non-empty
string, `observed_at` is UTC RFC3339, `horizon_days` is a non-negative integer,
and `value` is any JSON value. The importer never attempts entity, SKU, or
timestamp matching: an explicit decision ID is the only association key.

## Policy contract and label derivation

The checked-in default policy lives at
`glassbox/eval/policies/reconciliation_v1.toml`; `--policy PATH` is CLI-only.
Each policy has a non-empty `policy_version`, a positive `maturity_days` value
(initially 30), and one or more rules. A rule identifies an `agent_name`, a
`decision_type`, an `outcome_type`, a JSON pointer into the decision
recommendation, a list of positive recommendation scalar values, a JSON pointer
into the outcome value, and one positive outcome scalar value.

For an accepted row, Glassbox resolves exactly one rule using the stored
decision agent/name type and the row outcome type. It reads the two pointers:

- recommendation value is in `positive_recommendation_values` → predicted
  positive; otherwise predicted negative;
- outcome value equals `positive_outcome_value` → realized positive; otherwise
  realized negative.

The two booleans yield `tp`, `fp`, `tn`, or `fn`. Missing/non-scalar pointers,
zero/multiple applicable rules, or an invalid policy are import rejections;
Glassbox does not guess. This is policy-driven interpretation of opaque data,
not agent-specific source code.

The importer stores the derived label and the exact reconciliation policy
version/hash alongside each outcome. A reconciliation report only aggregates
labels carrying the requested policy hash; different policy revisions are never
silently combined. Updating a policy therefore requires importing a new source
export under that policy before it becomes a report cohort.

## Persistence and replay safety

P4 adds migration 005 to rebuild `outcomes` with `source_id`,
`reconciliation_policy_version`, and `reconciliation_policy_hash`. Existing
legacy rows are preserved with these three new columns `NULL`; new CLI imports
must supply all three. A partial unique index enforces unique non-null
`source_id` values. The existing outcome ID remains a Glassbox-generated ULID
and outcomes remain append-only.

For each line, the repository validates its typed submission and performs the
following within its own SQLite transaction:

- absent `source_id`: append one outcome with a new ULID;
- existing `source_id` and identical caller-supplied content (including policy
  provenance and derived label): return the existing outcome as an idempotent
  replay;
- existing `source_id` with different content: reject the line without writes.

Multiple source events may describe a decision over time. The report chooses
the most recently observed compatible outcome for each `(decision_id,
outcome_type)` by parsed `observed_at`, then outcome ID. The report policy
selects the applicable outcome type, so each decision contributes at most one
label to its confusion matrix.

## Commands and reject handling

```shell
glassbox outcomes import \
  --input outcomes.jsonl \
  --rejects outcomes.rejects.jsonl \
  --policy glassbox/eval/policies/reconciliation_v1.toml

glassbox outcomes report \
  --policy glassbox/eval/policies/reconciliation_v1.toml
```

`--rejects` is required. The import reads the input sequentially; each valid
line commits independently, while each rejected line produces exactly one JSONL
object with its one-based input line number, optional source ID, and a
non-sensitive reason code. Reject reasons include malformed JSON,
invalid_shape, unknown_decision, no_matching_rule, ambiguous_rule, invalid
pointer, and source_id_conflict. A blank line is ignored. Reject output is
written atomically through a sibling temporary file and rename, so a failed
reject-file write produces no ambiguous partial reject artifact.

The importer returns `0` when all non-blank lines are accepted or idempotent
replays, `1` when it completes with one or more rejected lines, and `2` for an
operational failure (unreadable input/database, invalid policy, or reject-file
write failure). It prints a compact JSON summary to stdout. Report completion
returns `0`; invalid policy/database/input returns `2`.

## Reconciliation report

`glassbox outcomes report` captures one UTC `as_of` instant. A decision is
mature when `decided_at <= as_of - maturity_days`. The report shows:

- mature decision count, labelled count, and coverage;
- `tp`, `fp`, `tn`, and `fn` counts plus precision and recall when their
  denominators are non-zero;
- labels grouped by `agent_name` and `decision_type`;
- operational override rate grouped by `decision_type`, calculated over mature
  decisions independently of outcome coverage;
- the policy version/hash, `as_of`, and maturity-days provenance.

Younger decisions are excluded from both missing-outcome and label metrics. A
mature decision with no compatible outcome remains unlabelled and lowers
coverage, but it does not enter precision or recall. Empty denominators render
as `null` in JSON, never zero.

## Boundaries and security

The parser and reconciliation calculation live in a single new public module
under `glassbox.eval`; it may import `glassbox.store` but no production package.
The repository owns SQL and never imports eval. The CLI imports this module
lazily inside its `outcomes` branch. There is no HTTP route or provider call in
P4. Error output never includes raw SQLite diagnostics, database paths, or the
source line's outcome value.

## Verification

Tests must prove strict JSONL parsing, rule/pointer evaluation, every confusion
matrix label, maturity and null-denominator behavior, policy-hash isolation,
idempotent replay, conflicting replay rejection, source decision FK rejection,
reject-file atomicity, and report override-rate grouping. Migration/schema tests
must verify unique source IDs, provenance constraints, strict timestamps, and
read-only open behavior. CLI tests must cover exact exit codes and ensure no
other CLI command imports the reconciliation module at load time. The normal
project test, lint, type, import-linter, and diff checks remain release gates.
