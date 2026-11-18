# P5 Drift-Evidence CI Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Verify an operator-reviewed drift snapshot in trusted-base GitHub Actions without opening a database or running drift in CI.

**Architecture:** A pure glassbox.eval.drift_gate module validates exact JSON evidence and a TOML policy against one UTC clock. A lazy glassbox drift verify-report branch exposes it; a separate trusted-base workflow invokes it. The verifier is isolated from drift calculation, SQLite, providers, and delivery.

**Tech Stack:** Python 3.11+, stdlib json/tomllib, argparse, GitHub Actions YAML, pytest, Ruff, mypy, import-linter.

## Global Constraints

- CI never opens SQLite, calculates drift, invokes providers, sends Discord, accesses credentials, or checks out PR code.
- Evidence paths are .glassbox/ci/drift-report.json and .glassbox/ci/drift-gate-policy.toml.
- The first policy accepts only drift_v2 and exactly confidence and decision_type signals; trace latency and cost remain excluded.
- healthy, watch, and insufficient_data exit 0; drift_detected exits 1; operational evidence failure exits 2.
- Checkout must use ${{ github.event.pull_request.base.sha || github.sha }} with persist-credentials: false.
- An absent report is an exit-2 failure; no report is fabricated.
- Preserve P2.4, trace-latency enablement, provider execution, scheduler, dashboard, and judge gate unchanged.

---

## File Structure

| File | Responsibility |
| --- | --- |
| glassbox/eval/drift_gate.py | Pure strict report/policy parser and safe result. |
| glassbox/cli.py | Lazy drift verify-report branch. |
| tests/eval/test_drift_gate.py | Schema, freshness, output, and safety tests. |
| tests/test_cli.py | CLI exit/output/import-isolation tests. |
| pyproject.toml, tests/test_architecture.py | Verifier import boundary. |
| .glassbox/ci/*, .github/workflows/* | Public policy/example and trusted workflow. |
| tests/test_drift_ci_workflow.py | Static plus executable workflow-contract tests. |
| .gitignore, README.md, TODO.md | File boundary, operator guide, roadmap state. |

---

### Task 1: Pure compact drift-evidence verifier

**Files:**
- Create: glassbox/eval/drift_gate.py
- Create: tests/eval/test_drift_gate.py

**Interfaces:**
- DriftGateError(ValueError)
- DriftGateResult(...).exit_code and .to_dict()
- verify_drift_gate_report(report_path: Path, policy_path: Path, *, clock: Callable[[], datetime]) -> DriftGateResult

- [ ] **Step 1: Write the failing verifier tests**

Create fixtures with this report and policy:

~~~python
REPORT = {
    "format_version": "drift_gate_report_v1", "status": "healthy", "reason": None,
    "generated_at": "2026-11-17T12:00:00Z",
    "agent_name": "replenishment-triage-ai", "policy_version": "drift_v2",
    "policy_hash": "a" * 64, "baseline_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
    "signals": [
        {"name": "confidence", "status": "healthy", "reason": None,
         "baseline_count": 500, "recent_count": 200},
        {"name": "decision_type", "status": "watch", "reason": None,
         "baseline_count": 500, "recent_count": 200},
    ],
}
POLICY = """format_version = "drift_gate_policy_v1"
max_age_seconds = 604800
expected_report_format = "drift_gate_report_v1"
expected_agent_name = "replenishment-triage-ai"
expected_policy_version = "drift_v2"
expected_signals = ["confidence", "decision_type"]
"""
~~~

Assert valid `to_dict()` output is exactly the validated report fields plus
`age_seconds` (and retains the validated signal name/status/reason/count
records), never file paths or parser details; its initial age is 0. Parametrize:

~~~python
[("healthy", None, 0), ("watch", None, 0),
 ("insufficient_data", "recent_window_too_small", 0),
 ("drift_detected", None, 1)]
~~~

For an extra key, duplicate JSON key, unknown/reordered/missing signal, status/reason mismatch, non-Z, future, stale, wrong policy, wrong agent, and PRIVATE_DECISION_CONTENT, assert:

~~~python
with pytest.raises(DriftGateError, match="Unable to verify drift report"):
    verify_drift_gate_report(report, policy, clock=lambda: NOW)
~~~

- [ ] **Step 2: Run the failing test**

Run:

~~~shell
.venv/bin/pytest -q tests/eval/test_drift_gate.py
~~~

Expected: collection fails because glassbox.eval.drift_gate does not exist.

- [ ] **Step 3: Implement the verifier**

Use stdlib only. Strictly accept:

~~~python
_REPORT_KEYS = frozenset({"format_version", "status", "reason", "generated_at",
    "agent_name", "policy_version", "policy_hash", "baseline_id", "signals"})
_SIGNAL_KEYS = frozenset({"name", "status", "reason", "baseline_count", "recent_count"})
_POLICY_KEYS = frozenset({"format_version", "max_age_seconds",
    "expected_report_format", "expected_agent_name", "expected_policy_version",
    "expected_signals"})
_REASONS = {
    "healthy": frozenset({None}), "watch": frozenset({None}),
    "drift_detected": frozenset({None}),
    "insufficient_data": frozenset({"baseline_not_created",
        "policy_changed_requires_rebaseline", "baseline_window_too_small",
        "recent_window_too_small", "baseline_has_no_variance"}),
}
~~~

Copy judge-gate techniques for unique object parsing, exact fields, bounded ASCII identifiers, strict Z timestamps, and a 64-lowercase-hex policy hash. Require the report agent to equal `expected_agent_name`, a non-null baseline_id except for insufficient_data, positive counts, and exact policy signal order/set. Convert every parsing/file/type error to:

~~~python
raise DriftGateError("Unable to verify drift report") from None
~~~

exit_code is 1 only for drift_detected.

- [ ] **Step 4: Verify and commit**

~~~shell
.venv/bin/pytest -q tests/eval/test_drift_gate.py
.venv/bin/ruff check glassbox/eval/drift_gate.py tests/eval/test_drift_gate.py
.venv/bin/mypy glassbox/eval/drift_gate.py
git add glassbox/eval/drift_gate.py tests/eval/test_drift_gate.py
git commit -m "feat: verify compact drift gate reports"
git push origin main
~~~

### Task 2: Lazy CLI and import isolation

**Files:**
- Modify: glassbox/cli.py, tests/test_cli.py, pyproject.toml, tests/test_architecture.py

**Interfaces:** glassbox drift verify-report --report PATH --policy PATH emits sorted compact JSON and exits 0, 1, or 2. Existing drift --agent and drift baseline do not import the verifier.

- [ ] **Step 1: Write failing CLI and boundary tests**

Add a drift_gate_files fixture using Task 1 data. Assert:

~~~python
assert cli.main(["drift", "verify-report", "--report", str(report),
                 "--policy", str(policy)]) == 0
assert json.loads(capsys.readouterr().out)["status"] == "healthy"
~~~

Monkeypatch builtins.__import__; fail if it imports glassbox.eval.drift, glassbox.store, Discord modules, glassbox.eval.judge, or dotenv. Missing paths must return 2, empty stdout, and exactly:

~~~text
glassbox: unable to verify drift report
~~~

Add a drift-gate-dependencies expected/registered import-linter contract for glassbox.eval.drift_gate, forbidding drift, store, judge/counterfactual/shadow/Discord modules, SDK, collector, web, and ops.

- [ ] **Step 2: Run failing tests**

~~~shell
.venv/bin/pytest -q tests/test_cli.py -k drift_verify_report
.venv/bin/pytest -q tests/test_architecture.py -k drift_gate
~~~

Expected: FAIL because parser branch and contract are absent.

- [ ] **Step 3: Add the lazy branch and contract**

Add verify-report under the drift subparser. It has required --report and --policy destination verify_policy, leaving arguments.policy unchanged. Before importing glassbox.eval.drift, add:

~~~python
if arguments.command == "drift" and arguments.drift_command == "verify-report":
    from glassbox.eval.drift_gate import DriftGateError, verify_drift_gate_report
    try:
        result = verify_drift_gate_report(
            arguments.report, arguments.verify_policy, clock=lambda: datetime.now(UTC)
        )
    except DriftGateError:
        print("glassbox: unable to verify drift report", file=sys.stderr)
        return 2
    print(json.dumps(result.to_dict(), sort_keys=True, separators=(",", ":")))
    return result.exit_code
~~~

Add the exact forbidden contract to pyproject.toml; update expected contract count from 9 to 10.

- [ ] **Step 4: Verify and commit**

~~~shell
.venv/bin/pytest -q tests/test_cli.py -k 'drift_verify_report or drift_command'
.venv/bin/pytest -q tests/test_architecture.py -k drift_gate
.venv/bin/lint-imports
.venv/bin/ruff check glassbox/cli.py tests/test_cli.py tests/test_architecture.py
.venv/bin/mypy glassbox
git add glassbox/cli.py tests/test_cli.py pyproject.toml tests/test_architecture.py
git commit -m "feat: add drift gate verifier command"
git push origin main
~~~

### Task 3: Trusted-base workflow and public operator contract

**Files:**
- Create: .glassbox/ci/drift-gate-policy.toml, .glassbox/ci/drift-report.json.example, .github/workflows/drift-evidence-gate.yml, tests/test_drift_ci_workflow.py
- Modify: .gitignore, README.md, TODO.md

**Interfaces:** Produces GitHub check Drift evidence gate over trusted-base compact evidence only.

- [ ] **Step 1: Write failing public-contract/workflow tests**

Assert policy parses exactly:

~~~python
{"format_version": "drift_gate_policy_v1", "max_age_seconds": 604800,
 "expected_report_format": "drift_gate_report_v1",
 "expected_agent_name": "replenishment-triage-ai",
 "expected_policy_version": "drift_v2",
 "expected_signals": ["confidence", "decision_type"]}
~~~

Assert example uses literal GENERATED_AT_UTC; .gitignore permits only policy/report/example and ignores .glassbox/ci/private.json. Require workflow text:

~~~text
pull_request_target:
push:
workflow_dispatch:
permissions:
  contents: read
ref: ${{ github.event.pull_request.base.sha || github.sha }}
persist-credentials: false
python -m pip install .
python -m glassbox drift verify-report --report .glassbox/ci/drift-report.json --policy .glassbox/ci/drift-gate-policy.toml
## Drift evidence gate
~~~

Forbid secrets, GLASSBOX_, sqlite, artifact, cache, discord, judge, drift --agent, pull_request:, pull_request.head, and drift-report.json.example. Execute its shell step from a temporary directory with missing evidence; require exit 2, generic stderr, and refresh summary text.

- [ ] **Step 2: Run failing test**

~~~shell
.venv/bin/pytest -q tests/test_drift_ci_workflow.py
~~~

Expected: FAIL because policy, example, and workflow are absent.

- [ ] **Step 3: Implement the public CI contract**

Create exact policy above. Create documentation-only example: insufficient_data / baseline_not_created, literal GENERATED_AT_UTC, the policy's fixed agent name, drift_v2, 64 a hash, null baseline, and two insufficient-data signals.

Mirror calibrated-judge-gate.yml with:

~~~bash
set +e
python -m glassbox drift verify-report --report .glassbox/ci/drift-report.json --policy .glassbox/ci/drift-gate-policy.toml > drift-gate-result.json
status=$?
set -e
{
  printf '## Drift evidence gate\n\n~~~json\n'
  cat drift-gate-result.json
  printf '\n~~~\n'
  if [ "$status" -eq 2 ]; then
    printf '\nEvidence is missing, invalid, stale, or future-dated. Refresh the operator-reviewed drift report on the protected base branch.\n'
  elif [ "$status" -eq 0 ] && python -c 'import json, sys; sys.exit(json.load(sys.stdin)["status"] != "insufficient_data")' < drift-gate-result.json; then
    printf '\nNeutral: insufficient data meant no drift gate was applied.\n'
  fi
} >> "$GITHUB_STEP_SUMMARY"
exit "$status"
~~~

Add .gitignore exceptions for policy/live report/example. Document projection, local verification, seven-day age, statuses, base-SHA trust, and no database/provider/PR-code. Mark only Wire CI workflow automation complete and add one unchecked operator-report refresh task. Do not alter P2.4, trace latency, or judge-report TODO items.

- [ ] **Step 4: Verify and commit**

~~~shell
.venv/bin/pytest -q tests/test_drift_ci_workflow.py
.venv/bin/ruff check .
.venv/bin/mypy glassbox
.venv/bin/lint-imports
git diff --check
.venv/bin/pytest -q
git add .glassbox/ci/drift-gate-policy.toml .glassbox/ci/drift-report.json.example \
  .github/workflows/drift-evidence-gate.yml tests/test_drift_ci_workflow.py \
  .gitignore README.md TODO.md
git commit -m "feat: add trusted drift evidence CI gate"
git push origin main
~~~

## Plan self-review

- **Coverage:** Task 1 implements safe strict evidence; Task 2 creates lazy isolation; Task 3 creates trusted-base wiring, operator files, and documentation.
- **Safety:** no task reads a database in CI, uses credentials, runs drift, contacts a provider, transfers artifacts, or lets PR code control a gate.
- **Consistency:** DriftGateError, DriftGateResult, and verify_drift_gate_report are defined in Task 1 and consumed under those names later.
- **Scope:** trace latency and human P2.4 evidence remain outside this plan.

