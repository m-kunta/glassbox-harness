# Glassbox

Glassbox is a local-first Python tracing harness for supply-chain agent decisions.
It provides a non-invasive tracing SDK, bounded fail-open collection, and local
SQLite trace inspection.

## Trace an existing agent

Decorate an agent entry point with `@glassbox.trace`, then configure the SDK
with a local collector. The included helper demonstrates the lifecycle used by
the replenishment agent:

```python
from pathlib import Path

from examples.wrap_existing_agent import configure_tracing, run_with_tracing

collector = configure_tracing(Path("glassbox.sqlite3"))
result = run_with_tracing(agent.run, collector, enriched_exceptions)
```

When tracing is disabled with `GLASSBOX_ENABLED=0`, SDK operations are no-ops
and the wrapped agent result is unchanged. Inspect a saved trace without
modifying its database:

```shell
glassbox --database glassbox.sqlite3 trace <trace-id>
```

Run the P0 overhead acceptance benchmark (30 baseline and 30 instrumented
runs by default) with:

```shell
python benchmarks/p0_overhead.py
```

The benchmark output and the P0 approval status are recorded in
[`docs/p0-ergonomics-report.md`](docs/p0-ergonomics-report.md).

## Capture redacted model content

Content capture is opt-in. Configure a content-addressed blob store, then pass
LLM prompt/completion content to a span or explicitly capture a trace input.
Glassbox applies configured redaction hooks before hashing and storing content;
events retain only the resulting blob references.

```python
from glassbox.store.blobs import BlobStore

glassbox.init(agent="triage", version="1.0", collector=collector, blob_store=BlobStore("blobs"))

with glassbox.span("llm.complete", kind="llm", prompt=prompt, completion=response):
    call_model()
glassbox.capture_input(request_payload)
```

## Run the replenishment evaluation suite

The independently versioned replenishment agent owns its adapter and golden
cases. From that repository, install its evaluation-only dependencies and run:

```shell
python -m pip install -r requirements-eval.txt
glassbox eval --suite goldens/replenishment_triage/manifest.yaml
```

## Run the local planner server

The P2.1 server is a loopback-only security foundation. Set
`GLASSBOX_LOCAL_ACCESS_TOKEN` in the process environment to a secret of at
least 32 characters, then start it:

```shell
export GLASSBOX_LOCAL_ACCESS_TOKEN='replace-with-a-secret-from-your-existing-secret-tool'
glassbox serve --port 8787
```

Open `http://127.0.0.1:8787/login` and enter the same token. Glassbox does not
load `.env` files or bind the server to a network interface. Set
`GLASSBOX_DATABASE` when the database is not `glassbox.sqlite3` in the current
directory. The server verifies that an existing database has the current schema
before it binds. After login, `/` lists decisions, `/decision/<id>` shows a
Decision Card, and `/trace/<id>` shows trace timing data. Each Decision Card
includes an authenticated append-only feedback ledger. Feedback
records planner assessment (`agree`, `disagree`, or `uncertain`) for later P3
calibration; it does not modify a decision or create an operational override.

Set `GLASSBOX_OPERATOR_NAME` to record an accountability label on operational
accept, modify, and reject actions; it defaults to `local-planner`. Overrides
are append-only: each new action supersedes the current action for that
decision, while a modified action records its corrected recommendation JSON.

## Export one Decision Card

Write a self-contained, read-only HTML snapshot without starting the server:

```shell
glassbox --database glassbox.sqlite3 export --decision <decision-id> --output decision-card.html
```

Use `--overwrite` to replace an existing artifact. Optionally add
`--live-base-url http://127.0.0.1:8787` to include a link back to the live
card. Exports contain inline CSS and no JavaScript, session state, or feedback
form; feedback and operational actions remain available only in the live app.

## Run the calibrated LLM judge

The judge scores recorded decision reasoning against the `reasoning_quality_v1`
rubric and gates on agreement with human labels before its results are
trusted. Install the base judge extra plus whichever provider you use:

```shell
python -m pip install -e '.[judge]'         # Ollama only; no extra SDK needed
python -m pip install -e '.[judge-claude]'  # + anthropic
python -m pip install -e '.[judge-openai]'  # + openai
python -m pip install -e '.[judge-gemini]'  # + google-genai
```

Configure the judge in a `.env` file in the **project root only** (the
directory you run `glassbox` from) — a `.env` in any other directory,
including an agent's own working directory, is never read. Any value already
set in the process environment always takes precedence over the same key in
`.env`. Required keys:

- `GLASSBOX_JUDGE_PROVIDER` — one of `claude`, `openai`, `gemini`, `ollama`.
- `GLASSBOX_JUDGE_MODEL` — the provider's model identifier.
- `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` — whichever
  matches the selected provider (`claude`, `openai`, `gemini` respectively).
  Ollama needs no credential.
- `GLASSBOX_JUDGE_OLLAMA_URL` — required only for `provider=ollama`; for
  example `http://127.0.0.1:11434`.

Run it against decisions from the last 7 days:

```shell
glassbox --database glassbox.sqlite3 judge --since 7d
```

`--since` takes a positive whole number followed by `m` (minutes), `h`
(hours), or `d` (days) — for example `30m`, `12h`, `7d`. `--max-cases N`
caps how many candidate decisions are judged in one run. `--allow-self-judge`
bypasses the refusal that otherwise blocks judging a decision whose own
recorded LLM spans used the same (normalized) model as the configured judge —
without it, that decision is skipped with an error rather than silently
self-graded.

**Egress disclosure and consent.** Claude, OpenAI, and Gemini are remote
providers: before any request leaves this machine, the command prints the
provider, model, constructed base URL, and how many decisions (calibration
backlog, recent-gated, and total de-duplicated) are about to be judged, then
refuses to proceed — exiting `2` without making any network call — unless you
pass `--confirm-egress`. A loopback Ollama endpoint (`localhost`, `127.0.0.1`,
or `::1`) never leaves the machine, so it never requires `--confirm-egress`;
the same disclosure is still printed for visibility. A non-loopback Ollama
URL is treated as remote and requires confirmation like any other provider.

**Human labels.** Human reasoning-quality scores come from the live app's
Decision Card feedback form, an integer from 1 (unsupported by the cited
evidence) through 5 (precise, complete, evidence-grounded reasoning); see
[`glassbox/eval/rubrics/reasoning_quality_v1.md`](glassbox/eval/rubrics/reasoning_quality_v1.md)
for the full rubric.

**Status and exit codes.** The command prints one compact JSON `JudgeReport`
to stdout and returns:

- `0` — the run's status is `passed`, or `uncalibrated` without
  `--require-calibrated`.
- `1` — the run's status is `failed`, or `uncalibrated` **with**
  `--require-calibrated` (use this flag in CI once you require the judge to
  already be calibrated).
- `2` — a preflight error: invalid or missing configuration, an unconfirmed
  remote provider, or a database read failure. No provider request is made.

A run is `uncalibrated` until the cohort (provider/model/rubric) has at least
**30** human/judge score pairs across all runs with linear weighted kappa
agreement of at least **0.60**, and this run's own gated (recent) set has at
least **10** successful judgments with a failure rate no higher than **5%**.
Once calibrated, the run's status is `failed` if the gated set's mean score
falls below **3.5**, otherwise `passed`.

## Monitor agent drift

Drift monitoring compares an agent cohort's recent decision and trace behavior
with an immutable historical baseline. Materialize a baseline once enough
historical telemetry exists, then create an audit snapshot or inspect the
live read-only report:

```shell
glassbox drift baseline --agent replenishment-triage-ai
glassbox drift --agent replenishment-triage-ai
glassbox serve
# visit http://127.0.0.1:8787/drift after local login
```

The checked-in policy is
[`glassbox/eval/policies/drift_v1.toml`](glassbox/eval/policies/drift_v1.toml).
Use `--policy PATH` only with the CLI when testing or adopting a different
versioned policy; the local web app always uses the checked-in default.
Baselines are immutable. Re-run baseline creation with `--supersede-baseline`
only when deliberately replacing an existing policy cohort with a new,
linked baseline.

CLI reports are persisted audit snapshots. The `/drift` view is calculated
now, uses a read-only database connection, and never records a drift run.
Both views keep confidence and decision type at decision level, while latency
and cost remain trace-level so batch size is visible rather than silently
treated as per-decision cost.

## Import and reconcile deferred outcomes

Import observed outcomes from JSONL, then print a read-only reconciliation
report for the same database:

```shell
glassbox outcomes import \
  --input outcomes.jsonl \
  --rejects outcomes.rejects.jsonl
glassbox outcomes report
```

Both commands use `GLASSBOX_DATABASE` or `glassbox.sqlite3` by default. Select
another database with `glassbox --database PATH outcomes ...`. The checked-in
policy is
[`glassbox/eval/policies/reconciliation_v1.toml`](glassbox/eval/policies/reconciliation_v1.toml);
both subcommands accept `--policy PATH` for a different versioned policy.

Each nonblank input line must explicitly identify a persisted decision by its
`decision_id`; Glassbox does not infer a match from a SKU or another business
key. For the checked-in replenishment policy, a row looks like:

```json
{"source_id":"warehouse-stockout-001","decision_id":"01ARZ3NDEKTSV4RRFFQ69G5FAV","outcome_type":"stockout_occurred","observed_at":"2026-10-05T12:00:00Z","horizon_days":30,"value":{"occurred":true}}
```

Use the actual decision ULID and a UTC observation timestamp. The default
policy matches agent `replenishment-triage`, decision type `triage`, and outcome
type `stockout_occurred`: recommendation actions `expedite` and `order` predict
a positive outcome, and `value.occurred=true` is a realized positive. Glassbox
derives `tp`, `fp`, `tn`, or `fn` from the recorded recommendation and observed
value; callers do not supply labels.

`source_id` identifies the external observation within the database. Repeating
the same source ID and payload under the same policy is an idempotent replay
with no new outcome row; reusing it with a different payload or policy
provenance is rejected as `source_id_conflict`. Each
accepted row retains its policy version and hash. Reports select the latest
compatible outcome per decision from the exact policy-hash cohort, so labels
from changed policies or legacy unlabelled outcomes do not mix into metrics.

`--rejects` is required, including for imports with no rejected rows. The
importer commits accepted rows independently and atomically replaces this
artifact with JSONL records containing the input line number, source ID when
available, and reason code. A successful all-accepted import produces an empty
reject file. Keep the input, database, and reject paths distinct; consult the
reject artifact after a partially successful import before correcting and
replaying rejected observations.

The default maturity window is 30 days from the decision timestamp, measured
at report time. Younger decisions are excluded. Mature decisions without a
compatible label remain in the coverage denominator. Reports include policy
version/hash, maturity and labelled counts, coverage, confusion counts,
precision, recall, and override rate, with breakdowns by decision type and
agent. Metrics with zero denominators are JSON `null`.

Outcomes commands return these exact process exit codes:

- `0` — import completed with zero rejected rows (including replay-only imports),
  or report completed successfully. The command prints its JSON result.
- `1` — import completed with at least one rejected row. Accepted rows are
  retained, and the command prints accepted, replayed, and rejected counts.
- `2` — invalid command arguments or a command-level failure, such as an invalid
  policy, unreadable input/database, or inability to write the reject artifact.
  Check stderr; no successful result JSON is printed. A failure after processing
  begins may leave already accepted rows committed.

P4 ships this local CLI import/report workflow. Dashboard generation, alerts,
scheduling, and CI automation remain deferred.

## Development

Use Python 3.11 or newer, then install the development extras and run the checks:

```shell
python -m pip install -e '.[dev]'
pytest --import-mode=importlib -q
ruff check .
mypy glassbox
lint-imports
```
