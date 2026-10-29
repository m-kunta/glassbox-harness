# Discord Drift Alerts final-review hardening

## Scope and inherited state

Continuation on `main` of the interrupted final-review pass, based on HEAD `bccbfcd`.
All inherited modifications were inspected and preserved; no reset, checkout, or
reversal was performed in the shared tree. There was no existing final-fixes report.
The unrelated `.superpowers/brainstorm/` artifacts remain untouched and uncommitted.

Completed the five required findings without adding dependencies or changing the
released migration, delivery-claim transaction, schema protections, or append-only
finalization behavior.

## Changes

### 1. Payload bounds and safe omission

- Agent and policy-version metadata have a fixed 128-character bound. Oversized
  values are omitted in full with a fixed marker, rather than exposing a prefix
  of arbitrary metadata. Baseline identity is bounded at the canonical 26 characters.
- Policy hashes use the requested 12-character prefix.
- Signal names are restricted to the four known operational signals. At most four
  fields are emitted; individual field values are bounded at 256 characters.
- Optional report URLs are revalidated as loopback and omitted if UTF-8 size exceeds
  512 bytes. Sensitive details/version counts never enter the constructed payload.
- The JSON-size check uses default ASCII escaping, so non-ASCII expansion is
  included. Fields are omitted if the payload exceeds the 8,192-byte budget with
  128 bytes reserved for the validated persisted run ID added by delivery.
- Regression coverage includes large ASCII values, large emoji values, boundary
  Unicode values, oversized links, original safe compact fields, suppressed mentions,
  and all four signal metrics with maximum-size agent/policy emoji metadata. The
  latter checks the final delivery payload including the run ID and a Unicode URL.

### 2. Cross-invocation recovery

- The CLI now discovers prior eligible attempted deliveries after the current drift
  report has been persisted. Recovery is scoped to the exact agent and hashed
  configured destination; the current run is excluded.
- Discovery inspects at most 100 recent persisted runs and returns at most three
  candidates. It skips unattempted runs, known delivered runs, and active pending
  claims younger than 30 seconds.
- Recovery reconstructs only persisted operational metadata and metrics. It passes
  each original persisted run ID into the existing atomic claim/send/finalize path;
  it does not call the drift calculator or retrieve raw decision content.
- Claim-time checks still enforce delivered/in-progress idempotency under concurrent
  invocations. Existing stale claims are finalized as `failed/stale_claim` before a
  new attempt is created. Failure of one recovered delivery does not stop the rest.
- Integration tests execute three separate CLI invocations with distinct newly
  persisted IDs and prove failed/stale original runs retry once, retain identical
  payload/metrics, preserve audit history, and never repost after success. Active
  and delivered originals remain untouched; exception/token text stays out of output.
- Additional coverage proves the three-candidate cap, stable newest-first ordering,
  destination/agent isolation, omission of never-attempted runs, and exclusion of
  sensitive stored result details from recovery snapshots.

### 3. Base-install configuration

- Replaced the unconditional optional `python-dotenv` import with a small stdlib-only
  parser for the three consumed Discord settings in the explicit project-root `.env`.
- Supports single-line quoted/unquoted values, `export`, and trailing comments.
  Never searches parent/cwd dotenv files, mutates the process environment, or
  expands variables. Nonblank process values retain precedence over file values.
- A fresh Python subprocess deliberately blocks the `dotenv` module, then imports
  configuration and delivery modules and resolves valid process configuration.

### 4. Whole-request wall-clock deadline

- Each HTTP request executes in a short-lived stdlib subprocess. The parent applies
  its monotonic remaining deadline to `communicate`, covering resolver, connection,
  TLS, and response-header work, rather than relying solely on socket inactivity.
- On deadline, cancellation, or other exit, the child is killed if still active and
  synchronously reaped. No detached thread or network process survives the call.
- The worker uses `ProxyHandler({})` and denies redirects, sends one POST, reads no
  response body, suppresses stderr/exception text, and returns only status plus a
  length-bounded retry header. Credentials/payload go through stdin, not argv.
- Deterministic coverage launches a real child with a slow injected DNS resolver
  and verifies bounded return plus a reaped child. A loopback server continuously
  streams response headers within each socket timeout; the request still stops at
  its overall deadline. Existing real-loopback proxy and redirect protections pass.
- The private transport worker is covered by the web/SDK/collector import boundaries.

### 5. Retry/timing validation

- Retry fallback is `min(0.25, caller_cap)`, including zero and sub-quarter-second
  caps. Existing finite valid Retry-After handling and absolute two-second cap remain.
- Request timeouts and retry caps reject negative, NaN, and infinite values before
  claiming a delivery. Every individual delivery attempt still sends at most two
  requests and waits at most once.

## Verification and red/green evidence

First inspected `git diff`, current implementations, config/HTTP/CLI/architecture
coverage, and the approved Discord design/plan. The inherited focused suite passed
322 tests once loopback server permissions were available. A sandbox-only run failed
36 HTTP tests solely because local socket bind was blocked; this was an environment
restriction, not an observed product regression.

The inherited changes initially failed static validation: seven lint findings and
10 mypy errors. Fixed import ordering/line lengths and added precise payload/signal
union annotations. No production behavior was changed for these static repairs.

An isolated temporary copy of the current sources/tests was created, then only its
Discord adapter/config/CLI files were replaced with their pre-fix HEAD versions.
The shared working tree was never reverted. Ran the inherited regression selection
against that tree. For invalid-timing checks, a test-only local transport failure was
injected so the intentionally broken implementation could never contact Discord.
All HTTP endpoints in the red run were loopback. Result:

- **24 failed, 2 passed, 191 deselected** (expected red).
- Three payload regressions observed payload sizes above 8,192 bytes.
- Ten retry fallback cases observed 0.25 seconds exceed zero/0.05-second caller caps.
- Six timing cases observed missing ValueError validation.
- Resolver cancellation test observed no cancellable/reaped child.
- Streaming-header test observed a request exceed the deadline and return normally.
- Base-install test observed the original unconditional dotenv import fail.
- Failed/stale CLI tests observed the original run posted only once instead of retrying.
- Active/delivered CLI negative controls passed under the pre-fix code.

Temporary red-check script/log (local diagnostic artifacts):
`/tmp/glassbox-discord-red-check.py` and `/tmp/glassbox-discord-red-check.log`.

Fresh final green verification:

- `.venv/bin/pytest -q`: **1,259 passed**, two existing Starlette/httpx/anyio
  deprecation warnings, 18.06 seconds. Includes migration, schema, delivery
  concurrency, CLI secrecy, and HTTP regressions.
- `.venv/bin/ruff check .`: **all checks passed**.
- `.venv/bin/mypy glassbox`: **no issues in 42 source files**.
- `.venv/bin/lint-imports`: **six contracts kept, zero broken**.
- `git diff --check`: clean.

No real Discord request was made during verification. Nothing was pushed.

## Practical limits and concerns

- Recovery preserves the explicitly approved lazy-import boundary: it runs only
  following a newly persisted aggregate `drift_detected` result. Healthy/watch/
  insufficient-data commands do not configure or send Discord traffic. Thus a failed
  original alert waits for a later detected invocation, rather than any healthy run.
- Recovery is deliberately bounded to three prior candidates among the newest 100
  runs for that agent. It is not an unbounded historical queue or background scheduler.
- The two-request maximum is per run delivery attempt. Up to three recovered runs
  plus the current run permit at most eight HTTP requests per eligible invocation;
  with default per-request deadlines and maximum accepted retry delays this adds
  at most roughly 24 seconds of network/wait budget, plus process/DB overhead.
- A timed-out or interrupted remote request may already have reached Discord before
  its acknowledgment was lost. As before, only known delivered runs are guaranteed
  not to repost; remote exactly-once delivery cannot be guaranteed by a webhook.
- Hard process cleanup requires OS scheduling/reaping overhead outside the nominal
  timeout; tests allow a small margin and verify that the worker is gone on return.
- The two full-suite deprecation warnings are unrelated to these changes.
