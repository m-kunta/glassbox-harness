# P5: Credential-free drift-evidence CI gate design

**Status:** Draft for review

## Purpose

Add the smallest safe GitHub Actions gate for an operator-reviewed Glassbox
drift result. The gate verifies a compact report generated outside CI; it does
not calculate drift. It therefore never opens a Glassbox database, receives
telemetry, invokes a provider, sends an alert, or uses credentials.

This is deliberately parallel to the existing calibrated-judge gate, but
remains a separate contract: judge evidence and drift evidence have different
provenance, statuses, and refresh workflows.

## Approved approach

An operator runs the existing `glassbox drift` command in the approved local
environment, reviews the persisted result, and projects only a safe summary
into:

```text
.glassbox/ci/drift-report.json
```

The checked-in policy lives beside it:

```text
.glassbox/ci/drift-gate-policy.toml
```

Both are protected-base inputs. For a pull request, the workflow checks out
the PR base SHA and runs the verifier, policy, and report from that checkout.
A PR cannot alter its own gate outcome by changing its workflow, verifier,
policy, or report. For a `main` push or manual dispatch, the selected trusted
revision is checked instead.

The report is initially absent. That is an operational failure rather than an
invented healthy result; a real operator-generated report is required before
the gate becomes meaningful.

## Compact report and policy

The report has an exact, versioned JSON schema. It contains only:

- `format_version`, `generated_at`, `agent_name`, `policy_version`,
  `policy_hash`, `baseline_id`, aggregate `status`, and aggregate `reason`;
- one entry for each monitored signal, containing its fixed signal name,
  status, reason, baseline count, and recent count.

It contains no database path, raw evidence, decision identifiers, entity
values, version mix, metric values, thresholds, trace timing, provider data,
or free-text diagnostics. Unknown, duplicate, missing, incorrectly typed, or
excessively long fields are rejected. UTC timestamps use the same strict
`...Z` grammar used by the judge gate. Identifiers are bounded ASCII values.

The TOML policy specifies the expected report format, a maximum report age,
the expected drift policy version, and the exact enabled signal set. These
choices are version-controlled and therefore part of the audited gate
contract. A new drift policy or monitored-signal set requires a reviewed policy
update and a fresh report; CI never compares reports across policy cohorts.

The initial maximum age is seven days, matching the judge-evidence policy.
Future-dated evidence is rejected. The verifier captures one UTC clock instant
for the entire freshness decision.

## CI result semantics

| Report status | CI exit | Meaning |
| --- | --- | --- |
| `healthy` or `watch` | `0` | Reviewed evidence does not require a blocking response. |
| `insufficient_data` | `0` | Neutral: monitoring could not evaluate the required population. |
| `drift_detected` | `1` | Reviewed evidence detected drift and blocks the check. |
| Missing, malformed, stale, future-dated, or policy-incompatible evidence | `2` | Operational failure: refresh or correct the trusted report. |

The workflow summary uses fixed wording and verifier-produced safe scalar
fields only. In particular, an `insufficient_data` result prominently says no
drift gate was applied and names its bounded reason; it is not presented as
healthy.

## Architecture

Introduce a pure verifier under `glassbox.eval`, exposed only through a lazy
CLI branch:

```text
glassbox drift verify-report --report PATH --policy PATH
```

It reads just the supplied JSON report and TOML policy, validates them into
immutable typed models, checks one clock value, and emits a compact JSON result
to stdout. Public failures are a generic stderr diagnostic that never includes
paths, report contents, exception text, or untrusted data.

The verifier must not import the drift calculator, database/repository,
provider adapters, SDK, collector, web/auth, scheduler, Discord, or
counterfactual modules. `glassbox.cli` imports it only inside the `drift
verify-report` handler. Import-linter explicitly names every verifier module
and preserves this isolation.

The new workflow is a checked-in YAML file with only `contents: read`, pinned
official checkout/setup actions, `persist-credentials: false`, and base-package
installation. It has no cache, artifact transfer, secrets, `GLASSBOX_*`
environment values, database, provider extra, report-generation command, or
PR-code checkout. It appends the verifier's compact result to
`GITHUB_STEP_SUMMARY` and preserves the verifier exit code.

## Operator workflow

1. Run `glassbox drift` locally under the approved policy and review the
   resulting operational report.
2. Copy only the validated compact drift-gate fields into
   `.glassbox/ci/drift-report.json`; preserve the command's actual evaluation
   time rather than editing a timestamp.
3. Verify the compact report locally with `glassbox drift verify-report`.
4. Review and merge the report refresh to the protected base branch.
5. CI thereafter evaluates the trusted base report until it expires or a
   newer policy/report replaces it.

The workflow cannot create a baseline, run drift, refresh evidence, retry
Discord delivery, install a scheduler, or infer a healthy result from missing
or insufficient local data.

## Verification

Tests must prove:

- exact report/policy schemas, strict timestamps, safe identifier limits, and
  duplicate-key rejection;
- all four valid drift statuses and the exact exit behavior above;
- stale, future, missing, malformed, wrong-policy, wrong-agent, and
  enabled-signal-set mismatch failures;
- output and workflow summary never contain arbitrary report content;
- regular `drift`, `serve`, `export`, and judge paths do not import the
  verifier;
- import-linter blocks every forbidden verifier dependency;
- the workflow checks out only its trusted base revision, uses no secrets or
  optional extras, and preserves the verifier's exit code.

## Non-goals

- Computing drift, opening SQLite, or uploading telemetry in GitHub Actions.
- Provider calls, credentials, remote egress consent, or Discord delivery.
- Replacing the local scheduler, dashboard, drift CLI, or calibrated-judge
  gate.
- Enabling trace-latency monitoring before real telemetry satisfies its sample
  floors.
- Completing the pending real-planner P2.4 usability gate.
