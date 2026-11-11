# Credential-free calibrated-judge CI gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a credential-free GitHub Actions gate that verifies trusted,
precomputed calibrated-judge evidence and blocks only a calibrated failure.

**Architecture:** A pure `glassbox.eval.judge_gate` module validates a compact,
strict JSON report against a checked-in TOML policy and returns a safe typed
result. `glassbox judge verify-report` lazily imports that module and maps only
the validated report status to CI exit codes. A `pull_request_target` workflow
checks out only the base revision, so a candidate PR cannot alter the workflow,
policy, report, or verifier that evaluates it.

**Tech Stack:** Python 3.11+, stdlib `json`/`tomllib`, existing CLI and
import-linter, pytest, GitHub Actions, PyYAML for workflow-structure tests.

## Global Constraints

- The gate consumes only `.glassbox/ci/judge-report.json` and
  `.glassbox/ci/judge-gate-policy.toml`; it never opens a Glassbox database,
  calls a provider, loads credentials, or makes an application network call.
- Valid report status `passed` and `uncalibrated` exit `0`; valid `failed`
  exits `1`; missing, malformed, stale, future, unknown, or
  provenance-incomplete reports exit `2`.
- `uncalibrated` is explicitly neutral: the verifier must retain its safe
  reason, but no `--require-calibrated` option is used by CI.
- The initial policy has `max_age_seconds = 604800` (seven days), expected
  report format `judge_gate_report_v1`, and rubric `reasoning_quality_v1`.
- Report JSON has exactly these root keys: `format_version`, `status`,
  `reason`, `generated_at`, `provider`, `model`, and `rubric_version`. Unknown
  or missing keys are invalid; no decision IDs, evidence, prompts, provider
  URLs, completions, database paths, or free text are accepted.
- Valid status/reason pairs are:
  `passed/calibrated`, `failed/mean_score_below_threshold`, and
  `uncalibrated/{too_few_labels,kappa_undefined,kappa_below_threshold,gated_set_too_small,failure_rate_too_high}`.
- `generated_at` is a strict UTC timestamp ending in `Z`, with 0–6 fractional
  digits. One captured UTC clock determines age; future reports and reports
  older than the policy maximum are operational errors.
- Provider/model/rubric are nonblank ASCII identifiers of at most 128
  characters matching `[A-Za-z0-9._:+/-]+`; reason and format-version are
  enumerated fixed strings. The verified output may contain only these
  validated scalar values plus integer age.
- `glassbox.cli` imports verifier code only in `judge verify-report`; ordinary
  `judge --since`, trace, export, serve, dashboard, drift, outcomes,
  counterfactual, and scheduler commands must not load it.
- Add a dedicated import-linter contract for every verifier module. It forbids
  `glassbox.eval.judge`, judge config/provider/models, SDK, collector, web,
  ops, shadow, counterfactual, and Discord modules.
- The workflow uses `pull_request_target` and checks out
  `${{ github.event.pull_request.base.sha || github.sha }}` only; it never
  checks out PR code. It has `permissions: {contents: read}`, no `secrets`, no
  database/artifact/cache, and no optional provider extras.
- Third-party actions are SHA-pinned: checkout v4.2.2 is
  `actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683`; setup-python
  v5.6.0 is
  `actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065`.
- Do not enable trace-latency monitoring, alter P2.4 usability status, add a
  production report, use real provider credentials, or create an activity-only
  commit.

---

## File structure

| File | Responsibility |
| --- | --- |
| `glassbox/eval/judge_gate.py` | Pure policy/report parsing, strict validation, freshness calculation, and safe typed verification result. |
| `tests/eval/test_judge_gate.py` | Unit tests for accepted reports, invalid inputs, exact exit semantics, and data-minimizing output. |
| `glassbox/cli.py` | Lazy `judge verify-report` dispatch while retaining the existing judge-run interface. |
| `tests/test_cli.py` | CLI exit/output/error and lazy-import regression tests. |
| `pyproject.toml` / `tests/test_architecture.py` | Dedicated verifier isolation contract and exact registered-contract assertions. |
| `.glassbox/ci/judge-gate-policy.toml` | Reviewed seven-day policy, expected report format, and rubric. |
| `.glassbox/ci/judge-report.json.example` | Safe schema example only; never treated as live evidence. |
| `.github/workflows/calibrated-judge-gate.yml` | Trusted-base GitHub Actions wiring. |
| `tests/test_ci_workflow.py` | Static workflow security/wiring regression tests. |
| `.gitignore`, `README.md`, `TODO.md` | Track only intended policy/report paths and document operator/bootstrap scope. |

## Task 1: Pure report verifier and policy contract

**Files:**
- Create: `glassbox/eval/judge_gate.py`
- Create: `tests/eval/test_judge_gate.py`

**Interfaces:**
- Produces `JudgeGateError(ValueError)`, immutable `JudgeGateResult`, and
  `verify_judge_gate_report(report_path: Path, policy_path: Path, *,
  clock: Callable[[], datetime]) -> JudgeGateResult`.
- `JudgeGateResult.to_dict()` produces only `status`, `reason`,
  `generated_at`, `age_seconds`, `provider`, `model`, and `rubric_version`.

- [ ] **Step 1: Write failing pure-verifier tests**

  Add helpers that write this exact valid report and policy:

  ```python
  REPORT = {
      "format_version": "judge_gate_report_v1",
      "status": "uncalibrated",
      "reason": "too_few_labels",
      "generated_at": "2026-11-10T12:00:00Z",
      "provider": "ollama",
      "model": "llama3.2",
      "rubric_version": "reasoning_quality_v1",
  }
  POLICY = """format_version = "judge_gate_policy_v1"\nmax_age_seconds = 604800\nexpected_report_format = "judge_gate_report_v1"\nexpected_rubric_version = "reasoning_quality_v1"\n"""

  def test_valid_statuses_have_exact_exit_categories(tmp_path: Path) -> None:
      now = datetime(2026, 11, 10, 12, tzinfo=UTC)
      assert verify_judge_gate_report(_report(tmp_path, "passed", "calibrated"), _policy(tmp_path), clock=lambda: now).exit_code == 0
      assert verify_judge_gate_report(_report(tmp_path, "uncalibrated", "too_few_labels"), _policy(tmp_path), clock=lambda: now).exit_code == 0
      assert verify_judge_gate_report(_report(tmp_path, "failed", "mean_score_below_threshold"), _policy(tmp_path), clock=lambda: now).exit_code == 1
  ```

  Add parametrized failures for missing/extra keys, unknown status, invalid
  status/reason pair, non-string provenance, disallowed provider/model text,
  wrong format/rubric, malformed/non-UTC/future/stale timestamps, malformed
  TOML, and missing files. Assert each raises `JudgeGateError` with no source
  path or report body. Add an output test injecting
  `PRIVATE_DECISION_CONTENT` as an unknown field and asserting it is absent
  from `result.to_dict()` and the exception string.

- [ ] **Step 2: Run the focused test to prove the missing boundary**

  Run: `.venv/bin/pytest tests/eval/test_judge_gate.py -v`

  Expected: FAIL during collection because `glassbox.eval.judge_gate` does not
  exist.

- [ ] **Step 3: Implement strict typed parsing and verification**

  Define fixed constants and frozen dataclasses:

  ```python
  class JudgeGateError(ValueError):
      """Safe failure while validating a precomputed judge gate report."""

  @dataclass(frozen=True)
  class JudgeGateResult:
      status: Literal["passed", "failed", "uncalibrated"]
      reason: str
      generated_at: str
      age_seconds: int
      provider: str
      model: str
      rubric_version: str

      @property
      def exit_code(self) -> int:
          return 1 if self.status == "failed" else 0
  ```

  Parse files with `Path.read_text(encoding="utf-8")`, `json.loads`, and
  `tomllib.loads`; normalize all `OSError`, `JSONDecodeError`, `TOMLDecodeError`,
  `TypeError`, and `ValueError` into content-free `JudgeGateError` messages.
  Require exact root-key sets and exact policy-key sets. Parse `generated_at`
  only after matching `^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d{1,6})?Z$`;
  use `datetime.fromisoformat(text.replace("Z", "+00:00"))`. Reject a clock
  that is not UTC-aware, negative age, and age over `max_age_seconds`. Return
  only normalized timestamp text and validated fields. Do not import judge,
  database, provider, configuration, or any web/SDK module.

- [ ] **Step 4: Run verifier checks**

  Run: `.venv/bin/pytest tests/eval/test_judge_gate.py -v && .venv/bin/ruff check glassbox/eval/judge_gate.py tests/eval/test_judge_gate.py && .venv/bin/mypy glassbox`

  Expected: all tests and static checks pass.

- [ ] **Step 5: Commit the independently testable verifier**

  ```bash
  git add glassbox/eval/judge_gate.py tests/eval/test_judge_gate.py
  git commit -m "feat: verify precomputed judge gate reports"
  git push origin main
  ```

## Task 2: Lazy CLI surface and verifier isolation

**Files:**
- Modify: `glassbox/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `pyproject.toml`
- Modify: `tests/test_architecture.py`

**Interfaces:**
- Consumes `verify_judge_gate_report` and `JudgeGateError` from Task 1.
- Produces `glassbox judge verify-report --report PATH --policy PATH` with safe
  JSON stdout and exit `0`, `1`, or `2`.

- [ ] **Step 1: Write failing CLI and import-boundary tests**

  Add a test that writes Task 1's valid files and asserts:

  ```python
  assert main(["judge", "verify-report", "--report", str(report), "--policy", str(policy)]) == 0
  assert json.loads(capsys.readouterr().out) == {
      "age_seconds": 0,
      "generated_at": "2026-11-10T12:00:00Z",
      "model": "llama3.2",
      "provider": "ollama",
      "reason": "too_few_labels",
      "rubric_version": "reasoning_quality_v1",
      "status": "uncalibrated",
  }
  ```

  Add `failed` => `1`; invalid report => `2`, empty stdout, and exactly
  `glassbox: unable to verify judge report\n` on stderr. Monkeypatch
  `builtins.__import__` to reject `glassbox.eval.judge_gate`, reload
  `glassbox.cli`, and prove `judge --since`, `trace`, `export`, `serve`, and
  `dashboard` retain their established behavior without importing it.

  Extend the architecture expectation with a ninth contract named
  `judge-gate-dependencies`, source `glassbox.eval.judge_gate`, forbidden
  modules:

  ```python
  [
      "glassbox.eval.judge", "glassbox.eval.judge_config",
      "glassbox.eval.judge_provider", "glassbox.eval.judge_models",
      "glassbox.eval.counterfactual", "glassbox.eval.counterfactual_config",
      "glassbox.eval.counterfactual_provider", "glassbox.eval.shadow",
      "glassbox.eval.discord", "glassbox.eval.discord_config",
      "glassbox.eval._discord_transport", "glassbox.sdk", "glassbox.collector",
      "glassbox.web", "glassbox.ops",
  ]
  ```

- [ ] **Step 2: Run the new CLI/boundary tests**

  Run: `.venv/bin/pytest tests/test_cli.py -k 'verify_report or judge_gate' -v && .venv/bin/pytest tests/test_architecture.py -k judge_gate -v`

  Expected: FAIL because `verify-report` and its import contract do not exist.

- [ ] **Step 3: Implement the backward-compatible parser branch**

  Replace `judge_command.add_argument("--since", required=True, ...)` with an
  optional `--since` and add:

  ```python
  judge_command.add_argument("judge_action", nargs="?", choices=["verify-report"])
  judge_command.add_argument("--report", type=Path)
  judge_command.add_argument("--policy", type=Path)
  ```

  Immediately after parsing, before any judge-config import:

  ```python
  if arguments.command == "judge" and arguments.judge_action == "verify-report":
      from glassbox.eval.judge_gate import JudgeGateError, verify_judge_gate_report
      if arguments.report is None or arguments.policy is None:
          print("glassbox: unable to verify judge report", file=sys.stderr)
          return 2
      try:
          result = verify_judge_gate_report(
              arguments.report, arguments.policy, clock=lambda: datetime.now(UTC)
          )
      except JudgeGateError:
          print("glassbox: unable to verify judge report", file=sys.stderr)
          return 2
      print(json.dumps(result.to_dict(), sort_keys=True, separators=(",", ":")))
      return result.exit_code
  ```

  For the existing judge-execution branch, keep its present provider loading
  and insert `if arguments.since is None: parser.error("judge requires --since")`
  before `_parse_duration` output is consumed. Do not accept `--report` or
  `--policy` in an ordinary judge run; reject that combination with parser exit
  `2` before provider configuration can load.

  Add the exact `judge-gate-dependencies` TOML contract and change the
  architecture test's registered count from eight to nine.

- [ ] **Step 4: Run focused CLI and architecture verification**

  Run: `.venv/bin/pytest tests/test_cli.py -k 'verify_report or judge_gate or judge_command' -v && .venv/bin/pytest tests/test_architecture.py -k judge_gate -v && .venv/bin/lint-imports`

  Expected: all pass and nine import contracts are kept.

- [ ] **Step 5: Commit the command boundary**

  ```bash
  git add glassbox/cli.py tests/test_cli.py pyproject.toml tests/test_architecture.py
  git commit -m "feat: add credential-free judge gate verifier command"
  git push origin main
  ```

## Task 3: Trusted-base workflow, operator files, and documentation

**Files:**
- Create: `.glassbox/ci/judge-gate-policy.toml`
- Create: `.glassbox/ci/judge-report.json.example`
- Create: `.github/workflows/calibrated-judge-gate.yml`
- Create: `tests/test_ci_workflow.py`
- Modify: `.gitignore`
- Modify: `README.md`
- Modify: `TODO.md`

**Interfaces:**
- Consumes Task 2's CLI and the exact policy/report locations.
- Produces an opt-in GitHub check that becomes active for trusted-base PR and
  main-push events once an operator has added `.glassbox/ci/judge-report.json`.

- [ ] **Step 1: Write failing static workflow/configuration tests**

  Test the policy via `tomllib` equals:

  ```python
  {
      "format_version": "judge_gate_policy_v1",
      "max_age_seconds": 604800,
      "expected_report_format": "judge_gate_report_v1",
      "expected_rubric_version": "reasoning_quality_v1",
  }
  ```

  Test the JSON example has exactly the seven global-constraint keys, is
  `uncalibrated/too_few_labels`, and uses `GENERATED_AT_UTC` rather than a
  timestamp that CI could mistake for real evidence. Assert `.gitignore`
  ignores `.glassbox/*` but explicitly unignores `.glassbox/ci/`, the policy,
  and `judge-report.json`.

  Read the workflow as text and assert it contains all of:

  ```text
  pull_request_target:
  push:
  permissions:
    contents: read
  actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
  actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065
  ref: ${{ github.event.pull_request.base.sha || github.sha }}
  persist-credentials: false
  python-version: "3.11"
  python -m pip install .
  python -m glassbox judge verify-report --report .glassbox/ci/judge-report.json --policy .glassbox/ci/judge-gate-policy.toml
  ```

  Assert it does not contain `secrets`, `GLASSBOX_`, `judge --since`,
  `--confirm-egress`, `sqlite`, `artifact`, `cache`, `pull_request:`, or a PR
  checkout expression.

- [ ] **Step 2: Run workflow tests to verify they fail**

  Run: `.venv/bin/pytest tests/test_ci_workflow.py -v`

  Expected: FAIL because no policy/example/workflow exists.

- [ ] **Step 3: Add the checked-in policy, safe example, and workflow**

  Write this exact policy:

  ```toml
  format_version = "judge_gate_policy_v1"
  max_age_seconds = 604800
  expected_report_format = "judge_gate_report_v1"
  expected_rubric_version = "reasoning_quality_v1"
  ```

  Write an example with literal `"GENERATED_AT_UTC"`, a fixed safe provider
  and model, `uncalibrated`, and `too_few_labels`; it is documentation only and
  must not be named `judge-report.json`.

  Create the workflow with `pull_request_target`, `push`, and
  `workflow_dispatch` triggers. Set only `contents: read`; check out the base
  SHA to the default workspace with `persist-credentials: false`; install `.`
  without provider extras; run the verifier; redirect its compact stdout to
  `judge-gate-result.json`; append that file under a fixed `## Calibrated judge
  gate` heading to `$GITHUB_STEP_SUMMARY`; and preserve the verifier's exit
  code. The workflow must not use the example file, a database, any secret, or
  PR code.

  Add gitignore exceptions:

  ```gitignore
  .glassbox/*
  !.glassbox/ci/
  !.glassbox/ci/judge-gate-policy.toml
  !.glassbox/ci/judge-report.json
  ```

  The actual `judge-report.json` remains absent until an operator produces a
  real report; until then the workflow intentionally exits `2` and explains
  that the evidence must be refreshed. Do not fabricate a timestamped result.

- [ ] **Step 4: Document the bootstrap and scope**

  Add a README section showing the operator's existing local judge command,
  the required compact seven-key report schema, the seven-day policy, base
  branch trust rule, status/exit table, and explicit statement that the
  workflow has no provider credentials, DB, artifact, or PR-code checkout.
  State that the example is never live evidence and a real report must be
  committed to activate a useful check.

  In TODO.md, mark only "Add CI gating once monitoring has an approved
  operational policy" complete. Add a new unchecked follow-up: "Record and
  periodically refresh an operator-generated judge gate report on the
  protected base branch." Leave P2.4 and trace-latency work unchanged.

- [ ] **Step 5: Run focused workflow and documentation verification**

  Run: `.venv/bin/pytest tests/test_ci_workflow.py tests/test_architecture.py -q && .venv/bin/ruff check . && .venv/bin/mypy glassbox && .venv/bin/lint-imports && git diff --check`

  Expected: all pass; static tests prove trusted-base, provider-free workflow
  wiring and all nine contracts are kept.

- [ ] **Step 6: Run the complete verification suite**

  Run: `.venv/bin/pytest -q && .venv/bin/ruff check . && .venv/bin/mypy glassbox && .venv/bin/lint-imports && git diff --check`

  Expected: all tests pass, Ruff and mypy are clean, nine contracts are kept,
  and whitespace checks pass.

- [ ] **Step 7: Commit the workflow and operator contract**

  ```bash
  git add .glassbox/ci/judge-gate-policy.toml .glassbox/ci/judge-report.json.example \
    .github/workflows/calibrated-judge-gate.yml tests/test_ci_workflow.py \
    .gitignore README.md TODO.md
  git commit -m "feat: add trusted calibrated judge CI gate"
  git push origin main
  ```

## Plan self-review

- **Spec coverage:** Task 1 implements strict, safe, credential-free report
  verification and neutral uncalibrated handling. Task 2 implements lazy CLI
  exits and isolation. Task 3 implements the trusted-base workflow, seven-day
  policy, bootstrap documentation, report tracking, and explicit absence of
  fabricated evidence.
- **Placeholder scan:** All runtime paths, accepted keys, status/reason pairs,
  time semantics, SHA pins, commands, test assertions, and commit messages are
  concrete. The deliberately literal `GENERATED_AT_UTC` example is a safety
  marker, not an unfilled requirement.
- **Type consistency:** Task 1's `JudgeGateResult`, verifier signature, and
  `exit_code` are consumed unchanged by Task 2; Task 2's command and Task 3's
  policy/report locations match exactly.
