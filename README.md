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

# After the provider reports its own known total for this trace:
glassbox.set_trace_cost_usd(provider_response.total_cost_usd)
```

Glassbox records a caller-supplied total only; it does not estimate prices from
model or token metadata. Omit the call when the provider does not expose a
trusted total; without the call, the trace cost remains unknown (`null`) and
Glassbox never substitutes zero.

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
glassbox drift baseline --agent replenishment-triage-ai \
  --start 2026-09-01T00:00:00Z --end 2026-10-01T00:00:00Z
glassbox drift --agent replenishment-triage-ai
glassbox serve
# visit http://127.0.0.1:8787/drift after local login
```

The checked-in policy is
[`glassbox/eval/policies/drift_v2.toml`](glassbox/eval/policies/drift_v2.toml).
Use `--policy PATH` only with the CLI when testing or adopting a different
versioned policy; the local web app always uses the checked-in default.
Choose historical UTC timestamps ending in `Z` for the half-open
`[start, end)` baseline window, with `start < end` and `end` no later than
creation time. The example dates are illustrative: use your own telemetry's
window, with at least 500 decisions for each default signal. A window that
overlaps the recent seven days is allowed with a warning, since shared samples
reduce sensitivity.
Baselines are immutable. Re-run baseline creation with `--supersede-baseline`
when deliberately changing the window for an existing agent/policy cohort;
this appends a linked baseline and preserves its predecessor.

The default monitors confidence and decision type. Latency and cost display
“Not monitored (disabled by policy).” Omitting a signal's table disables it;
an enabled signal with too few usable samples reports insufficient data.
Cost capture is available only through `set_trace_cost_usd`; the default drift
policy still leaves trace cost disabled, so enabling it later requires a
versioned policy change and a new baseline.

To opt into latency, copy the default policy to a versioned file and append:

```toml
[trace_latency_ms]
population = "trace"
algorithm = "cusum"
minimum_baseline_samples = 200
minimum_recent_samples = 100
transform = "log1p"
evaluation_samples = 100
reference_value = 0.5
warning_threshold = 5.2
alert_threshold = 7.0
```

Select that file with `--policy PATH` for both baseline and report commands.
The changed policy hash requires its own baseline. Enable latency only when
trace cadence supports 200 baseline and 100 recent completed, successful
traces. Historical latency is derived from start/end timestamps; new SDK
completion events also populate `latency_ms`. Incomplete and error traces
are excluded. CUSUM evaluates the most recent 100 samples in log space;
displayed latency summaries remain in raw milliseconds.

CLI reports are persisted audit snapshots. The `/drift` view is calculated
now, uses a read-only database connection, and never records a drift run.
Both views keep confidence and decision type at decision level. Optional latency
and cost use traces as their sample unit, with decisions-per-trace shown as
context for batch-size changes.

## Send Discord drift alerts

Discord incoming-webhook delivery is opt-in. Configure the following in the
Glassbox project-root `.env`, independent of the working directory, or in the
process environment. Nonblank process-environment values take precedence;
configuration does not modify the process environment or read a sibling agent's
`.env`. The examples below are non-real placeholders; replace the webhook ID
and token with your channel's values.

```dotenv
GLASSBOX_DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/WEBHOOK_ID/WEBHOOK_TOKEN
GLASSBOX_DISCORD_ALLOW_EGRESS=1
# Optional: link to this machine's report view only.
GLASSBOX_DISCORD_REPORT_URL=http://127.0.0.1:8787/drift
```

Treat the webhook URL as a secret: keep it out of source control, screenshots,
and shared logs. `.env` is gitignored. Glassbox never prints or persists the
raw URL or includes it in errors. The accepted form is exactly
`https://discord.com/api/webhooks/<numeric-id>/<token>`: the ID contains only
digits and the token contains only ASCII letters, digits, `_`, and `-`, with
no port, user information, query, fragment, or trailing slash. Alternate hosts
and schemes are rejected. Both a valid URL and the explicit egress gate
`GLASSBOX_DISCORD_ALLOW_EGRESS` set to exactly `1` are required to send the
operational summary to Discord; missing gates disable delivery.

The optional report URL must be HTTP(S) with an exact host of `localhost` or a
loopback IP address (including IPv6), without user information, whitespace,
backslashes, or an invalid/zero port. Remote hosts and lookalike hostnames such
as `localhost.example.com` are rejected; an invalid report URL is omitted from
the alert. A loopback link opens the viewer's own machine, so it is useful only
where that local Glassbox report is available; normal local login is required.

Run `glassbox drift --agent replenishment-triage-ai` as usual. Delivery occurs
automatically only after a `drift_detected` report is persisted. Baseline
creation, `healthy`, `watch`, insufficient-data reports, and the read-only
`/drift` page do not send alerts. The payload is a compact operational summary:
agent, drift-run ID, policy version/hash, report timestamp, detected signal
values, thresholds, sample counts, and the optional local report URL. It contains
no decision data, evidence, rationale, recommendations, feedback, or blob content;
Discord mentions are disabled.

Requests use direct no-proxy transport, ignoring environment proxy variables,
and do not follow redirects. Each request has a two-second timeout. Transport
errors, HTTP `429`, and `5xx` allow one retry, at most two requests in total,
with a delay capped at two seconds. A valid `Retry-After` within that cap is
honored; otherwise the delay is 0.25 seconds. Other `4xx` responses fail without
retrying. Any `2xx` response marks delivery successful.

Durable idempotency uses `(drift_run_id, destination_hash)`, where the destination
hash is SHA-256 of the exact webhook URL. SQLite records a `pending` claim before
network I/O and finalizes it as `delivered` or `failed`; completed audit rows are
immutable. Successful deliveries and active claims suppress duplicate attempts
for that same pair, including across process restarts. Failed deliveries may be
retried on a later invocation; a pending claim becomes stale after 30 seconds
and is finalized as `failed`/`stale_claim` before a new claim is created. A new
drift run or a changed destination is a different pair. If Discord accepted a
request before a crash prevented local finalization, a later stale-claim retry
can still duplicate the remote message. Audit rows retain only delivery metadata
and normalized error categories, never request payloads, response bodies, or
exception text.

Delivery failures are isolated from monitoring: the persisted report remains
authoritative, with unchanged drift exit semantics: `0` for a successfully
calculated report, including `drift_detected` or `insufficient_data`, and
`2` for command-level failures (such as invalid policy or baseline creation
with too few samples).
Detected-report JSON includes a concise `discord_delivery` status, reason, and
delivery ID without exposing secrets or remote response details. No scheduler
or background delivery service is started.

## Import and reconcile deferred outcomes

Import observed outcomes from JSONL, then print a read-only reconciliation
report for the same database. Select reconciliation v2 explicitly for both commands:

```shell
glassbox outcomes import \
  --input outcomes.jsonl \
  --rejects outcomes.rejects.jsonl \
  --policy glassbox/eval/policies/reconciliation_v2.toml
glassbox outcomes report --policy glassbox/eval/policies/reconciliation_v2.toml
```

Both commands use `GLASSBOX_DATABASE` or `glassbox.sqlite3` by default. Select
another database with `glassbox --database PATH outcomes ...`.
reconciliation_v1 remains the default for legacy compatibility:
[`glassbox/eval/policies/reconciliation_v1.toml`](glassbox/eval/policies/reconciliation_v1.toml).
The urgency rule described below requires explicit
`--policy glassbox/eval/policies/reconciliation_v2.toml` on both subcommands;
omitting it selects the legacy v1 action-matching rule. Both checked-in policies
remain available as versioned artifacts.

Each nonblank input line must explicitly identify a persisted decision by its
`decision_id`; Glassbox does not infer a match from a SKU or another business
key. For the explicitly selected v2 replenishment policy, a row looks like:

```json
{"source_id":"warehouse-stockout-001","decision_id":"01ARZ3NDEKTSV4RRFFQ69G5FAV","outcome_type":"stockout_occurred","observed_at":"2026-10-05T12:00:00Z","horizon_days":30,"value":{"occurred":true}}
```

Use the actual decision ULID and a UTC observation timestamp. The explicitly selected v2
policy matches agent `replenishment-triage`, decision type `triage`, and outcome
type `stockout_occurred`: recommendation `/urgency` values `CRITICAL` and
`HIGH` predict a positive outcome, and `value.occurred=true` is a realized positive. Glassbox
derives `tp`, `fp`, `tn`, or `fn` from the recorded recommendation and observed
value; callers do not supply labels.

Export negative observations explicitly too, for example:

```json
{"source_id":"warehouse-stockout-002","decision_id":"01ARZ3NDEKTSV4RRFFQ69G5FAV","outcome_type":"stockout_occurred","observed_at":"2026-10-05T12:00:00Z","horizon_days":30,"value":{"occurred":false}}
```

An absent record means unknown outcome, not “no stockout.” For this policy,
`horizon_days` must be exactly 30 and `observed_at` must fall between the
decision timestamp and that timestamp plus 30 days, inclusive.

`source_id` identifies the external observation within a policy-hash cohort. Repeating
the same source ID and payload under the same policy is an idempotent replay
with no new outcome row; reusing it with a different payload in that cohort
is rejected as `source_id_conflict`. Re-importing under a different policy
hash appends a new row, preserving the prior policy's result. Each
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

## Run an offline counterfactual prompt experiment

Install the counterfactual extra for your provider. The base extra supports a
local Ollama endpoint; remote providers need their matching SDK extra:

```shell
python -m pip install -e '.[counterfactual]'         # Ollama
python -m pip install -e '.[counterfactual-claude]'  # + anthropic
python -m pip install -e '.[counterfactual-openai]'  # + openai
python -m pip install -e '.[counterfactual-gemini]'  # + google-genai
```

Set `GLASSBOX_COUNTERFACTUAL_PROVIDER` to `ollama`, `claude`, `openai`, or
`gemini`, and set `GLASSBOX_COUNTERFACTUAL_MODEL` to the selected model. For
Claude, OpenAI, or Gemini, also set the corresponding `ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, or `GEMINI_API_KEY`. Ollama defaults to
`http://127.0.0.1:11434`; override it with `GLASSBOX_COUNTERFACTUAL_OLLAMA_URL`.
The counterfactual command reads these dedicated values from the process
environment or the `.env` in the Glassbox project root, independent of the
working directory. It never reads a sibling agent's `.env`. Nonempty process
values take precedence, and file values are not injected into the process
environment. `.env` is gitignored.

Use a checked-in prompt artifact such as
[`counterfactual_v1.md`](glassbox/eval/prompts/counterfactual_v1.md). Its TOML
front matter declares `prompt_version`; Glassbox also hashes the complete raw
file, so each run records both its stated version and exact content hash.
The body inserts the decision snapshot at `{{decision_snapshot_json}}`. Run a
bounded recent batch and retrieve its persisted report with:

```shell
glassbox --database glassbox.sqlite3 counterfactual run \
  --prompt glassbox/eval/prompts/counterfactual_v1.md \
  --since 7d \
  --blob-dir /path/to/redacted-blobs \
  --confirm-egress
glassbox --database glassbox.sqlite3 counterfactual report --run RUN_ID
```

`--blob-dir` is optional. Omit it when no eligible captured input should be
read; supply the location of the already redacted blob store only when the
prompt artifact also declares
`capture_input_contract = "input_only_no_prior_outputs"`. Only an eligible
trace input capture may enrich the provider input. Glassbox never adds an
earlier model completion, an LLM span prompt capture, or an agent output from
that trace. The shipped `counterfactual_v1.md` omits this contract, so the
example command records no capture enrichment until you copy the prompt,
declare the contract in its front matter, and select that copy with `--prompt`.
Cited structured evidence and alternatives remain available to
the experiment without blob capture. The provider input excludes the original
recommendation, original rationale, and realized outcome so the variant can
reason from the recorded decision facts.

Before the first provider call, the CLI discloses provider, model, destination,
prompt version/hash, selected-case count, and capture count. Remote providers
and non-loopback Ollama endpoints require `--confirm-egress`; loopback Ollama
still prints the disclosure but needs no confirmation. Provider requests may
send recorded evidence and eligible redacted input content to the disclosed
destination. Configuration and consent are checked before any provider call.

Each run is append-only. Results store structured snapshot facts, capture
reference IDs, and a hash of the rendered provider input; they do not store
capture content in SQLite. A report reconstructs the saved provider, model,
prompt, policy, case outcomes, and per-case failure diagnostics. A compatible
mature P4 outcome can label a hypothetical recommendation under the selected
reconciliation policy; an immature or missing outcome has no derived label.
The command defaults to
[`reconciliation_v2.toml`](glassbox/eval/policies/reconciliation_v2.toml),
whose urgency rule treats `CRITICAL` and `HIGH` as positive predictions.
Use `--outcome-policy PATH` to select another versioned policy; only outcomes
from that exact policy-hash cohort can enrich a result.

The report describes a **prompt-and-model counterfactual** unless recorded
source-model provenance proves provider and model parity for the selected
cases. It is a record of hypothetical responses, not an aggregate claim that
one prompt wins. The run command exits `0` when at least one case succeeds,
including a run with some failed cases. It exits `2` when no case succeeds,
after persisting and printing that auditable run; `report --run` can retrieve
it later. Preflight and command-level failures also exit `2`.

## Submit an explicit live counterfactual shadow

Live shadowing is an application-owned, fail-open integration. It evaluates an
approved prompt in the background and does not change the production decision,
agent return value, or exception. Install the existing counterfactual extras
shown above (`.[counterfactual]` for Ollama, or the matching
`.[counterfactual-claude]`, `.[counterfactual-openai]`, or
`.[counterfactual-gemini]` extra). Configure the same
`GLASSBOX_COUNTERFACTUAL_PROVIDER`, `GLASSBOX_COUNTERFACTUAL_MODEL`, provider
credential, and optional Ollama URL in the process environment or the `.env`
at the explicitly supplied Glassbox `project_root`. Nonempty process values
take precedence; a sibling agent's `.env` is never read.

Enable admission explicitly in the **process environment**:

```shell
export GLASSBOX_SHADOW_ENABLED=1
# Also required for Claude, OpenAI, Gemini, or non-loopback Ollama:
export GLASSBOX_SHADOW_ALLOW_EGRESS=1
```

Both flags must be exactly `1` for remote egress. Loopback Ollama needs only
`GLASSBOX_SHADOW_ENABLED=1`. Putting these flags only in `.env` does not enable
them, and the offline CLI's `--confirm-egress` does not authorize live egress.
Authorize only a reviewed provider destination and approved prompt artifact;
live submission has no interactive consent prompt.

Create one long-lived executor and validate the approved `ShadowArtifact`
during application setup, where artifact errors can be handled before serving
production traffic. Point it at the same database used by the tracing collector:

```python
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from glassbox.eval.counterfactual_provider import create_counterfactual_provider
from glassbox.eval.shadow import LiveShadowExecutor, ShadowArtifact, load_shadow_artifact

project_root = Path("/absolute/path/to/glassbox")
approved_artifact: ShadowArtifact = load_shadow_artifact(
    project_root / "glassbox/eval/prompts/counterfactual_v1.md",
    project_root / "glassbox/eval/policies/reconciliation_v2.toml",
)
shadow = LiveShadowExecutor(
    database_path=project_root / "glassbox.sqlite3",
    project_root=project_root,
    blob_dir=None,
    environ=os.environ,
    clock=lambda: datetime.now(UTC),
    monotonic_clock=time.monotonic,
    sleep=time.sleep,
    provider_factory=create_counterfactual_provider,
)


def on_completed_decision(decision_id: str) -> None:
    # The application invokes this hook only after DecisionScope.complete().
    shadow.submit(decision_id, approved_artifact)


# During application shutdown, inspect local counters and close admission:
# local_metrics = dict(shadow.diagnostics)
# shadow.shutdown(timeout=0.0)
```

Wire that hook explicitly for each selected decision after `DecisionScope.complete()`;
pass the exact recorded `DecisionEvent.decision_id`
from your integration. `complete()` returns no ID, and the SDK supplies no
automatic shadow hook. Do not submit an incomplete decision or create a new
executor per decision. Submission does not wait for source persistence or a
provider request; SDK, collector, and web code do not initiate shadow work.

All executors share a process-wide limit of **one active job, one queued job,
and ten accepted jobs over the process lifetime**. Completed jobs and executor
shutdown do not replenish that budget. Full-queue and lifetime-cap submissions
are dropped with local diagnostic counters and no audit rows. A worker waits
up to **five seconds** for the collector to persist the source decision; if it
never appears, no provider is constructed or called. Each provider request has
a **ten-second elapsed deadline**. A timed-out transport may still be finishing;
no overlapping bounded provider call is admitted until it returns.

`shadow.diagnostics` is a read-only snapshot of local counters.
`dict(shadow.diagnostics)` exposes these keys:

- Admission: `disabled`, `configuration_unavailable`, `egress_not_authorized`,
  `queue_full`, `lifetime_cap`.
- Execution failures: `source_decision_unavailable`, `provider_timeout`,
  `provider_failure`, `parse_failure`, `storage_failure`.
- Successful jobs: `completed`.

Operational failures are contained. `shutdown(timeout=...)` closes that executor's admission
and waits only for its accepted jobs; a finite timeout leaves unfinished daemon
work running, so process exit can abandon it.

Once the source decision exists, each executed shadow uses the append-only,
immutable `counterfactual_runs` / `counterfactual_results` audit history with
`execution_mode="live_shadow"`; existing offline history stays `offline`.
Retrieve a saved run with the same `counterfactual report --run RUN_ID` command.
Live results record `outcome_not_applicable`: there is no decision-time outcome
enrichment. The provider receives fact-only snapshot input, excluding the
original recommendation, rationale, confidence, feedback, and realized outcomes.
Earlier completions, LLM prompt captures, and agent outputs remain excluded;
optional redacted input enrichment requires the same declared input-only
contract as offline experiments. The example disables blob enrichment.

## Development

Use Python 3.11 or newer, then install the development extras and run the checks:

```shell
python -m pip install -e '.[dev]'
pytest --import-mode=importlib -q
ruff check .
mypy glassbox
lint-imports
```
