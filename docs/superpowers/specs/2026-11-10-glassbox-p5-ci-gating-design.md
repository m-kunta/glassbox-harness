# P5: Credential-free calibrated-judge CI gate design

**Status:** Approved for implementation planning

## Purpose

Add the smallest safe GitHub Actions quality gate for Glassbox's calibrated
reasoning-quality judge. The gate blocks only on a calibrated failed result.
It never calls an LLM provider, loads provider credentials, opens the Glassbox
database, or sends decision data to a network service.

This is deliberately a verifier of previously generated evidence, not another
execution surface for `glassbox judge`.

## Approved policy

An operator generates a compact JSON `JudgeReport` in an approved local or
controlled environment using the existing `glassbox judge` command. The
operator commits that report to:

```text
.glassbox/ci/judge-report.json
```

The checked-in policy lives alongside it at:

```text
.glassbox/ci/judge-gate-policy.toml
```

The initial policy has a seven-day maximum report age. It is part of reviewed
source control rather than an environment variable. The policy declares the
expected report format version and required provenance fields; it does not
contain credentials, provider URLs, decision IDs, evidence, prompts, or model
responses.

The verifier recognizes exactly three report statuses:

| Report status | CI result | Meaning |
| --- | --- | --- |
| `passed` | exit 0 | The judge is calibrated and the recent gated mean meets the accepted threshold. |
| `uncalibrated` | exit 0 | No quality gate was applied; CI summary prominently names the neutral reason. |
| `failed` | exit 1 | The calibrated quality gate failed and blocks the check. |

A missing, malformed, stale, unknown-version, or provenance-incomplete report
is an operational failure: exit 2. It is not silently reclassified as
`uncalibrated`.

## Trust and data boundary

The report is an operator-maintained assertion. A pull request must not be able
to manufacture its own passing assertion by changing the report, policy, or
verifier.

For `pull_request_target` events, GitHub uses the default branch's workflow definition
from the base repository. The workflow explicitly checks out
`github.event.pull_request.base.sha` in the runner's default workspace. It runs
the verifier and policy from that PR base commit and reads the report from the
same checkout. See [GitHub's security guidance](https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target)
for the distinction between the workflow definition and an explicit checkout.
Consequently, a candidate PR's changes to `.glassbox/ci/`, Python verification code, or the workflow cannot
change its own gate outcome. The workflow never checks out or executes PR
code. The report is refreshed through a separate reviewed update to the
protected base branch.

For protected-branch pushes, the workflow checks the committed main-branch
policy, verifier, and report at that commit. Branch protection remains the
control that governs who may change these gate inputs.

The verifier receives only the JSON report and policy. It does not read a
SQLite database or artifact, invoke a provider SDK, inspect environment
credentials, import the web/server/auth surface, or make an HTTP request. Its
GitHub summary contains only safe status, reason, report timestamp, age, and
judge provider/model/rubric provenance; it never prints arbitrary report JSON
or persisted decision content.

## Interfaces and architecture

Introduce a pure verifier boundary under `glassbox.eval`, for example:

```text
glassbox judge verify-report --report PATH --policy PATH
```

It parses the checked-in report and TOML policy into typed immutable models,
validates strict UTC `...Z` timestamps, checks freshness against a single UTC
clock value, and writes one compact safe JSON verification result to stdout.
The result has `status` (`passed`, `failed`, or `uncalibrated`), a bounded
machine-readable `reason`, report generation time, and non-sensitive
provenance. CLI stderr is a generic operational error only; it never includes
paths, report contents, credentials, or exception text.

`glassbox.cli` imports this verifier only inside the `judge verify-report`
branch. Existing `judge` execution continues to load its provider configuration
only when it is explicitly run. The verifier does not import judge provider
adapters/configuration, SDK, collector, web, auth, scheduler, Discord, or
counterfactual execution modules. Import-linter will enforce this boundary.

The GitHub workflow is a small checked-in YAML file. It checks out the
appropriate trusted revision, uses the repository's supported Python version,
installs the project without optional provider extras, invokes the verifier,
and appends the safe result to `GITHUB_STEP_SUMMARY`. It uses no secrets,
database artifacts, cache containing run data, remote actions beyond the
official pinned checkout/setup steps, or custom egress.

## Operator workflow

1. Run the existing judge in the approved environment, following its explicit
   egress-consent procedure when applicable.
2. Inspect the compact JSON output and commit only its safe report form to
   `.glassbox/ci/judge-report.json` with a policy-compatible timestamp and
   provenance.
3. Review and merge that refresh to the protected base branch.
4. PRs then verify the trusted base report. A fresh report may be needed after
   seven days; a stale report fails operationally rather than allowing an
   indefinite historical pass.

The workflow cannot create or refresh reports. It provides no automatic
calibration, provider invocation, report signing, database upload, or policy
promotion.

## Failure handling

- `failed` is a real quality result and returns exit 1.
- `uncalibrated` returns exit 0, but the summary explicitly states that no
  quality gate was applied and includes the judge-provided reason.
- Invalid file content, incompatible format/policy, stale clock value, missing
  provenance, or workflow setup failure returns exit 2.
- The workflow summary uses fixed wording plus validated scalar fields. It
  never embeds untrusted free text from a report.

## Verification

Tests must cover:

- all three valid statuses and their exact exits;
- strict schema/version/provenance validation;
- stale, future, malformed, missing, and unknown-status reports;
- one captured clock value and strict UTC timestamp handling;
- safe compact stdout/summary data and absence of arbitrary report content;
- lazy CLI imports proving regular `judge`, trace, export, and serve paths do
  not load the verifier or its policy parser;
- import-linter enforcement for every concrete verifier module;
- workflow wiring that uses a base revision for pull requests, provider-free
  installation, no secrets, and the verifier's documented paths.

The implementation plan will specify the exact report schema, TOML keys,
workflow event matrix, test fixtures, and import-contract module list.

## Non-goals

- Running a live provider or sending decision data from CI.
- Injecting provider credentials or egress confirmation into GitHub Actions.
- Requiring calibration before CI can pass.
- Uploading, creating, migrating, or reading a Glassbox database in CI.
- Trusting a report, policy, verifier, or workflow modified by the PR being
  checked.
- Replacing deterministic P1 evaluation, drift monitoring, counterfactual
  experiments, or human planner usability validation.
