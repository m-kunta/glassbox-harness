# Discord Drift Alerts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send one bounded, opt-in Discord incoming-webhook alert for each persisted `drift_detected` run without allowing delivery to alter monitoring truth.

**Architecture:** The drift runner saves its immutable run before the CLI considers notification. An eval-side adapter loads scoped config, atomically claims a delivery in SQLite, uses a no-proxy stdlib HTTP transport with one bounded retry, and finalizes the audit record. Every notification state is observational; existing drift JSON and exit semantics remain authoritative.

**Tech Stack:** Python 3.11+, SQLite/WAL, `urllib.request`, `python-dotenv`, pytest, Ruff, mypy, import-linter.

## Global Constraints

- Send only after a persisted run whose aggregate status is exactly `drift_detected`.
- Load only `GLASSBOX_DISCORD_*` names from project-root `.env` without mutating `os.environ`; non-empty process values win.
- Require both `GLASSBOX_DISCORD_WEBHOOK_URL` and `GLASSBOX_DISCORD_ALLOW_EGRESS=1`.
- Accept optional `GLASSBOX_DISCORD_REPORT_URL` only when it is an exact loopback HTTP(S) URL; omit an absent or unsafe link from the payload.
- Accept only `https://discord.com/api/webhooks/...`, with no credentials, query, fragment, or unsafe path.
- Use `urllib.request.build_opener(urllib.request.ProxyHandler({}))`; ambient proxy variables must not redirect delivery.
- Persist only a SHA-256 destination hash and normalized error categories—never a URL, payload, exception text, or response body.
- A known delivered `(drift_run_id, destination_hash)` never reposts. A failed delivery can retry on a later drift invocation.
- Make at most two requests: one initial request and one retry only for a transport failure, timeout, `429`, or `5xx`.
- `sdk`, `collector`, and `web` must not import the new Discord modules. Add no third-party dependency.
- Do not change P2.4 usability, latency enablement, bot support, scheduling, CI gating, dashboard generation, or unrelated TODOs.

---

## File structure

| File | Responsibility |
| --- | --- |
| `glassbox/store/migrations/010_discord_deliveries.sql` | Delivery-attempt table, indexes, and append-only protection. |
| `glassbox/store/schema.sql`, `glassbox/store/database.py` | Fresh-schema mirror and exact 009 → 010 migration fingerprint. |
| `glassbox/store/repository.py` | Typed delivery records plus atomic claim/finalize operations. |
| `glassbox/eval/discord_config.py` | Scoped dotenv/process configuration and exact URL validation. |
| `glassbox/eval/discord.py` | Safe payload, no-proxy sender, bounded retry, durable orchestration. |
| `glassbox/eval/drift.py`, `glassbox/cli.py` | Persisted run ID and post-persistence automatic invocation. |
| `tests/store/*`, `tests/eval/test_discord*.py`, `tests/test_cli.py`, `tests/test_architecture.py` | Regression coverage. |
| `README.md`, `TODO.md` | Operator instructions and one completed backlog item. |

### Task 1: Persist atomic delivery claims

**Files:**
- Create: `glassbox/store/migrations/010_discord_deliveries.sql`
- Modify: `glassbox/store/schema.sql`, `glassbox/store/database.py`, `glassbox/store/repository.py`
- Test: `tests/store/test_repository.py`, `tests/store/test_database.py`, `tests/store/test_schema.py`

**Interfaces:** Produces `DiscordDeliveryRecord`, `DiscordDeliveryClaim`, `Repository.claim_discord_delivery()`, and `Repository.finalize_discord_delivery()` for Task 2.

- [ ] **Step 1: Write failing claim/finalization tests.**

```python
def test_discord_delivery_claim_is_atomic_and_delivery_is_idempotent(
    drift_repository: Repository,
) -> None:
    run = _stored_drift_run(drift_repository)
    claimed = drift_repository.claim_discord_delivery(
        run.drift_run_id, "a" * 64, now=_utc("2026-10-24T12:00:00Z")
    )
    assert claimed.status == "claimed"
    assert drift_repository.claim_discord_delivery(
        run.drift_run_id, "a" * 64, now=_utc("2026-10-24T12:00:01Z")
    ).status == "in_progress"
    assert claimed.delivery is not None
    drift_repository.finalize_discord_delivery(
        claimed.delivery.delivery_id, status="delivered", error_category=None,
        finalized_at=_utc("2026-10-24T12:00:02Z"),
    )
    assert drift_repository.claim_discord_delivery(
        run.drift_run_id, "a" * 64, now=_utc("2026-10-24T12:00:03Z")
    ).status == "already_delivered"
```

Add tests for unknown-run foreign keys, invalid hashes/status/timestamps, a failed first attempt creating attempt 2, a distinct destination hash, delete rejection, finalized-row immutability, and stale pending recovery after 30 seconds.

- [ ] **Step 2: Prove tests fail.**

Run: `.venv/bin/pytest tests/store/test_repository.py -k discord -v`

Expected: FAIL because the delivery repository interface does not exist.

- [ ] **Step 3: Add the migration and schema fingerprint.**

Create a `discord_deliveries` table with `delivery_id` canonical ULID, restricted
`drift_run_id` FK, `destination_hash`, positive `attempt_number`, `pending|delivered|failed`
status, nullable `error_category`, and project-standard UTC `created_at`/`finalized_at` checks.
Use the exact state constraint:

```sql
CHECK ((status = 'pending' AND error_category IS NULL AND finalized_at IS NULL)
    OR (status = 'delivered' AND error_category IS NULL AND finalized_at IS NOT NULL)
    OR (status = 'failed' AND error_category IS NOT NULL AND finalized_at IS NOT NULL)),
UNIQUE (drift_run_id, destination_hash, attempt_number)
```

Add partial unique indexes for one successful and one pending row per `(drift_run_id, destination_hash)`. Add triggers rejecting delete and rejecting every update except changing one pending row's `status`, `error_category`, and `finalized_at` once. Mirror this DDL in `schema.sql`.

In `database.py`, add Discord table/index/trigger names to fingerprints. Rename the present current fingerprint helper to `_released_009_schema_objects()`, derive the new current schema by applying `010_discord_deliveries.sql`, and add the exact ordinary 009 → 010 migration branch plus the new fingerprint to `_migration_needed()`.

- [ ] **Step 4: Implement the repository boundary.**

Add exact types near existing drift records:

```python
DiscordDeliveryStatus = Literal["pending", "delivered", "failed"]
DiscordDeliveryClaimStatus = Literal["claimed", "already_delivered", "in_progress"]

@dataclass(frozen=True)
class DiscordDeliveryRecord: ...

@dataclass(frozen=True)
class DiscordDeliveryClaim:
    status: DiscordDeliveryClaimStatus
    delivery: DiscordDeliveryRecord | None
```

Implement:

```python
def claim_discord_delivery(
    self, drift_run_id: str, destination_hash: str, *, now: datetime,
    stale_after: timedelta = timedelta(seconds=30),
) -> DiscordDeliveryClaim: ...

def finalize_discord_delivery(
    self, delivery_id: str, *, status: Literal["delivered", "failed"],
    error_category: str | None, finalized_at: datetime,
) -> DiscordDeliveryRecord: ...
```

`claim_discord_delivery()` validates input then uses `BEGIN IMMEDIATE`: return delivered, return a non-stale pending claim, finalize a stale pending claim as `failed/stale_claim`, or insert next attempt number as pending. `finalize_discord_delivery()` updates exactly one pending row and raises `ValueError` otherwise. Reuse existing strict timestamp serialization/parsing and never accept arbitrary status/error strings.

- [ ] **Step 5: Verify Task 1.**

Run: `.venv/bin/pytest tests/store/test_repository.py -k discord tests/store/test_database.py tests/store/test_schema.py -q`

Expected: PASS including migration and read-only-current-schema assertions.

- [ ] **Step 6: Commit.**

```bash
git add glassbox/store/migrations/010_discord_deliveries.sql glassbox/store/schema.sql \
  glassbox/store/database.py glassbox/store/repository.py tests/store
git commit -m "feat: persist Discord delivery attempts"
```

### Task 2: Add sealed config and Discord sender

**Files:**
- Create: `glassbox/eval/discord_config.py`, `glassbox/eval/discord.py`
- Test: `tests/eval/test_discord_config.py`, `tests/eval/test_discord.py`
- Modify: `pyproject.toml`, `tests/test_architecture.py`

**Interfaces:** Consumes Task 1 claim/finalize APIs and produces `DiscordConfig`, `load_discord_config()`, `DiscordDeliveryOutcome`, and `deliver_drift_alert()` for Task 3.

- [ ] **Step 1: Write failing config tests.**

```python
def test_load_discord_config_requires_both_explicit_gates(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "GLASSBOX_DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/123/token\n",
        encoding="utf-8",
    )
    config = load_discord_config(tmp_path, {})
    assert config.status == "disabled"

def test_load_discord_config_accepts_exact_discord_webhook(tmp_path: Path) -> None:
    config = load_discord_config(tmp_path, {
        "GLASSBOX_DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/token",
        "GLASSBOX_DISCORD_ALLOW_EGRESS": "1",
    })
    assert config.status == "ready"
```

Test process-over-dotenv precedence, no mapping mutation, and rejection of HTTP, non-Discord hosts, userinfo, a port, query/fragment, or any path not beginning `/api/webhooks/`. Test that `GLASSBOX_DISCORD_REPORT_URL` accepts only an exact loopback HTTP(S) URL and is omitted rather than treated as an error when unsafe or absent.

- [ ] **Step 2: Prove config tests fail.**

Run: `.venv/bin/pytest tests/eval/test_discord_config.py -v`

Expected: FAIL because the config module does not exist.

- [ ] **Step 3: Implement `discord_config.py`.**

Use `dotenv_values(Path(project_root) / ".env", interpolate=False)` and resolve only nonblank scoped keys. Define:

```python
@dataclass(frozen=True)
class DiscordConfig:
    status: Literal["ready", "disabled", "invalid"]
    webhook_url: str | None
    report_url: str | None
    reason: Literal["missing_url", "egress_not_authorized", "invalid_url"] | None

def load_discord_config(project_root: Path, environ: Mapping[str, str]) -> DiscordConfig: ...
```

Absent gates return `disabled`; a supplied unsafe URL returns `invalid`; neither state raises or performs I/O.

- [ ] **Step 4: Write failing sender tests with real local receivers.**

Use `ThreadingHTTPServer` only in tests. Inject opener, sleep, and clock. Assert no call for non-drift reports; compact payload omission of sensitive fixture strings; initial success; `500`/`429` one retry; `400` no retry; transport error; and exactly two-call maximum. Exercise the private HTTP helper directly with a loopback test endpoint while `http_proxy` and `HTTP_PROXY` point at a distinct local proxy receiver; assert the request reaches only the intended receiver. Public configuration remains restricted to the Discord HTTPS URL. Assert a loopback-only report URL is included and any non-loopback URL is omitted.

- [ ] **Step 5: Implement `discord.py`.**

```python
@dataclass(frozen=True)
class DiscordDeliveryOutcome:
    status: Literal["disabled", "invalid", "skipped", "delivered", "failed"]
    reason: str | None
    delivery_id: str | None

def build_discord_payload(report: DriftReport, *, report_url: str | None) -> dict[str, object]: ...

def deliver_drift_alert(
    repository: Repository, report: DriftReport, drift_run_id: str,
    config: DiscordConfig, *, clock: Callable[[], datetime],
    sleep: Callable[[float], None], timeout_seconds: float = 2.0,
    retry_delay_cap_seconds: float = 2.0, opener: OpenerDirector | None = None,
) -> DiscordDeliveryOutcome: ...
```

Only build a payload after config is ready and the repository claim succeeds. Hash the UTF-8 URL via SHA-256. The payload may contain only fixed title, agent, run/policy provenance, drifted signal metrics/thresholds, report timestamp, and validated optional loopback URL. POST JSON using `ProxyHandler({})` by default and accept every `2xx`. Retry once for transport errors, timeouts, `429`, and `5xx`; use a finite `Retry-After` only if it is within the two-second cap, otherwise sleep `0.25`. Normalize final errors as `transport_error`, `http_429`, `http_5xx`, or `http_4xx`, finalize before returning, and never return exception detail.

Add `glassbox.eval.discord` and `glassbox.eval.discord_config` to all applicable import-linter forbidden lists, including `web-dependencies`. Keep dependencies unchanged.

- [ ] **Step 6: Verify and commit Task 2.**

Run: `.venv/bin/pytest tests/eval/test_discord_config.py tests/eval/test_discord.py tests/test_architecture.py -q`

Expected: PASS.

```bash
git add glassbox/eval/discord_config.py glassbox/eval/discord.py pyproject.toml \
  tests/eval/test_discord_config.py tests/eval/test_discord.py tests/test_architecture.py
git commit -m "feat: add Discord drift delivery adapter"
```

### Task 3: Integrate delivery after persistence

**Files:**
- Modify: `glassbox/eval/drift.py`, `glassbox/cli.py`
- Test: `tests/eval/test_drift.py`, `tests/test_cli.py`

**Interfaces:** Consumes Task 2 config/sender. Produces `DriftReport.persisted_run_id` and safe `discord_delivery` output metadata.

- [ ] **Step 1: Write failing persistence and CLI tests.**

```python
def test_persisted_drift_report_exposes_its_exact_run_id(path: Path, policy: DriftPolicy) -> None:
    report = run_drift_report(path, "agent", policy, clock=_fixed_clock, persist=True)
    assert report.persisted_run_id is not None
    assert _stored_drift_run_ids(path) == [report.persisted_run_id]
```

Test `persist=False` remains `None`. In CLI tests monkeypatch the Discord loader/sender: a detected run invokes sender with the exact ID; `watch` never loads config; disabled/failed outcomes exit `0`; JSON contains only outcome status/reason/delivery ID, not a URL.

- [ ] **Step 2: Prove tests fail.**

Run: `.venv/bin/pytest tests/eval/test_drift.py tests/test_cli.py -k 'discord or persisted_run_id' -v`

Expected: FAIL because persisted run provenance and delivery integration do not exist.

- [ ] **Step 3: Add run provenance.**

Add keyword-only `persisted_run_id: str | None = None` to `DriftReport` and add it to `to_dict()`. In `run_drift_report`, capture the returned `DriftRunRecord` and use `dataclasses.replace(report, persisted_run_id=record.drift_run_id)`. No ID exists without persistence or without a baseline.

- [ ] **Step 4: Add the CLI boundary.**

Only in the drift-report path, after `run_drift_report(..., persist=True)` returns a persisted `drift_detected` report, lazily import Discord modules. Load config with `Path(__file__).resolve().parents[1]`, open a writable `Database`, invoke `deliver_drift_alert`, and close in `finally`. Convert all delivery-boundary failures to `DiscordDeliveryOutcome("failed", "delivery_unavailable", None)` and keep return code `0`. Attach only `status`, `reason`, and `delivery_id` under `payload["discord_delivery"]`. Baseline creation and all non-detected reports must never load Discord configuration or create a transport.

- [ ] **Step 5: Verify and commit Task 3.**

Run: `.venv/bin/pytest tests/eval/test_drift.py tests/test_cli.py -q`

Expected: PASS.

```bash
git add glassbox/eval/drift.py glassbox/cli.py tests/eval/test_drift.py tests/test_cli.py
git commit -m "feat: deliver detected drift alerts automatically"
```

### Task 4: Document and verify operations

**Files:**
- Modify: `README.md`, `TODO.md`, `tests/test_architecture.py`

- [ ] **Step 1: Write a failing README contract test.**

Require documentation of both environment names, exact Discord host/path, automatic detected-only behavior, no-proxy transport, one retry, durable idempotency, and unchanged drift exit semantics.

- [ ] **Step 2: Prove the documentation test fails.**

Run: `.venv/bin/pytest tests/test_architecture.py -k discord -v`

Expected: FAIL because no Discord operator guide exists.

- [ ] **Step 3: Add safe operator documentation.**

Add these non-real placeholders to the README:

```dotenv
GLASSBOX_DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/WEBHOOK_ID/WEBHOOK_TOKEN
GLASSBOX_DISCORD_ALLOW_EGRESS=1
# Optional: link to this machine's report view only.
GLASSBOX_DISCORD_REPORT_URL=http://127.0.0.1:8787/drift
```

Explain root `.env` precedence, secret handling, exact loopback-only report-link validation, automatic detected-only delivery, compact payload, direct no-proxy transport, one retry, duplicate suppression, and failure isolation. Check only `Add Discord notification delivery for operational alerts.` in `TODO.md`.

- [ ] **Step 4: Run full verification.**

```bash
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/mypy glassbox
.venv/bin/lint-imports
git diff --check
```

Expected: all checks pass; existing third-party deprecation warnings may remain, but no new failures or warnings are accepted.

- [ ] **Step 5: Commit Task 4.**

```bash
git add README.md TODO.md tests/test_architecture.py
git commit -m "docs: document Discord drift alerts"
```

## Plan self-review

- **Coverage:** Tasks 1–4 cover storage/idempotency, scoped configuration and egress safety, automatic post-persistence invocation, and operator documentation.
- **Ambiguity resolved:** a `pending` claim is durable before network I/O; a stale pending row is finalized as `failed/stale_claim` before a new attempt, preventing permanent blockage after a crash.
- **Type consistency:** Task 1 provides records to Task 2; Task 2 provides outcome/config to Task 3; Task 3 provides the persisted run ID that binds the delivery to immutable monitoring evidence.
- **Scope:** no bot, scheduler, CI, dashboard, multiple destination, or agent-runtime change is included.
