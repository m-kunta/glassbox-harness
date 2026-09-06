# Glassbox P2.2 Read Experience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the authenticated P2.1 placeholder with a read-only, planner-readable decision queue, Decision Card, and trace view backed by the existing Glassbox database.

**Architecture:** `store` owns a separately opened strict-schema read-only SQLite connection and typed, parameterized query results. `web.read_service` opens and closes that connection per request, maps store records into immutable presentation models, and owns generic display policy such as confidence bands and citation grouping. FastAPI routes render only those models through autoescaped Jinja templates.

**Tech Stack:** Python 3.11+, SQLite WAL, FastAPI, Jinja2, pytest, Ruff, mypy, import-linter.

## Global Constraints

- Require Python `>=3.11`; Glassbox remains local-first with no external service or network egress.
- Read `GLASSBOX_DATABASE` only from the process environment, defaulting to `glassbox.sqlite3`; P2.2 opens it read-only.
- `Database.open_read_only()` uses SQLite URI `mode=ro`, requires an existing file, never creates directories/files, changes journal mode, initializes/migrates schema, commits, or writes application data.
- Preflight the strict schema before binding `glassbox serve`; a legacy, unknown, corrupted, or non-SQLite target is a clear startup failure without raw SQLite error text.
- A web request opens and closes its own read-only `Database`/`Repository`; never share a SQLite connection in `app.state`.
- Preserve committed WAL visibility for a writer in another process. Do not use `immutable=1`, because it hides uncheckpointed WAL data.
- Keep all persisted recommendations, rationale, evidence, alternatives, overrides, agent fields, and blob references autoescaped. Show blob references only; never load blob content.
- `glassbox.web` may import `store` but never `sdk`; `store` never imports `web`.
- Every query value is bound. The only sort aliases are `timestamp -> decided_at` and `confidence -> confidence`; raw query text is never SQL.
- Define confidence once in `web.read_models`: Low `[0.00, 0.50)`, Medium `[0.50, 0.80)`, High `[0.80, 1.00]`.
- A citation key identifies every evidence field in its group. Dangling citations and malformed override/span history render diagnostics rather than disappearing or crashing a page.
- Timestamp cursors carry the canonical `decided_at` text read from SQLite verbatim; never reformat a `datetime` for a SQLite TEXT equality predicate.
- P2.2 is read-only: do not add feedback submission, CSRF POST enforcement, HTMX behavior, static export, blob-content display, or planner usability testing.

---

## File structure

| Path | Responsibility |
| --- | --- |
| `glassbox/store/database.py` | Strict read-only connection factory and actionable schema errors. |
| `glassbox/store/repository.py` | Typed queue/detail/override database reads with bound filters and fixed SQL sort columns. |
| `glassbox/web/read_models.py` | Immutable web presentation types, confidence bands, and diagnostics. |
| `glassbox/web/read_service.py` | Per-request read orchestration and typed-event-to-view-model conversion. |
| `glassbox/web/server.py` | Database-path configuration/preflight and authenticated read routes. |
| `glassbox/web/templates/base.html` | Shared escaped layout and queue navigation. |
| `glassbox/web/templates/queue.html` | Filterable, paginated decision queue and empty state. |
| `glassbox/web/templates/decision_card.html` | Planner-readable decision, grouped evidence, citations, alternatives, and deferred-feedback notice. |
| `glassbox/web/templates/trace.html` | Trace metadata and nested span display. |
| `glassbox/web/templates/error.html` | Generic authenticated 404/503 pages with no SQLite/path disclosure. |
| `glassbox/web/static/glassbox.css` | One local stylesheet; no JavaScript framework or HTMX dependency in P2.2. |
| `tests/store/test_database.py` | New read-only factory, strict-schema, missing-file, and separate-process WAL tests. |
| `tests/store/test_repository.py` | Typed queue queries, fixed-sort validation, pagination, and effective override states. |
| `tests/web/test_read_models.py` | Confidence, generic recommendation, evidence-group, citation, override, and span-tree unit tests. |
| `tests/web/test_read_service.py` | Read-only per-request service behavior and data conversion tests. |
| `tests/web/test_server.py` | Authenticated queue/card/trace route rendering, escaping, empty/404/503 states. |
| `README.md`, `TODO.md` | Read-only operator behavior and P2.2 completion evidence. |

## Task 1: Strict read-only database factory and live-WAL proof

**Files:**
- Modify: `glassbox/store/database.py`
- Modify: `glassbox/store/__init__.py`
- Create: `tests/store/test_database.py`

**Interfaces:**
- Produces `ReadOnlyDatabaseError(RuntimeError)` for a missing, pre-strict, or unsupported read target.
- Produces `Database.open_read_only(path: Path | str, *, busy_timeout_ms: int = 5_000) -> Database`.
- `Database.open()` remains the only factory that can create/migrate/write a database.

- [ ] **Step 1: Write failing read-only factory tests.**

  Create `tests/store/test_database.py`. Define compact local helpers that create a current strict database with `Database.open()` and a released pre-strict database by executing `000_pre_strict_initial.sql`; do not depend on fixtures from another test module.

  Add the following tests:

  ```python
  def test_open_read_only_requires_an_existing_database(tmp_path: Path) -> None:
      missing = tmp_path / "missing.sqlite3"

      with pytest.raises(ReadOnlyDatabaseError, match="does not exist"):
          Database.open_read_only(missing)

      assert missing.exists() is False


  def test_open_read_only_accepts_the_current_strict_schema(tmp_path: Path) -> None:
      writable = Database.open(tmp_path / "glassbox.sqlite3")
      writable.close()

      readonly = Database.open_read_only(tmp_path / "glassbox.sqlite3")
      try:
          assert readonly.connection.execute("SELECT count(*) FROM decisions").fetchone()[0] == 0
          with pytest.raises(sqlite3.OperationalError, match="readonly"):
              readonly.connection.execute("UPDATE traces SET status = 'error'")
      finally:
          readonly.close()


  def test_open_read_only_rejects_pre_strict_schema(tmp_path: Path) -> None:
      path = _create_pre_strict_database(tmp_path / "legacy.sqlite3")

      with pytest.raises(ReadOnlyDatabaseError, match="writer-capable"):
          Database.open_read_only(path)
  ```

  Add a test that creates a SQLite database containing a conflicting `traces` table and asserts `ReadOnlyDatabaseError` mentions `unsupported schema`. Add another that writes arbitrary non-SQLite bytes to an existing file and asserts the same public exception without exposing the SQLite error text. Test `busy_timeout_ms=-1` raises the same `ValueError` contract as `Database.open()`.

- [ ] **Step 2: Run the new factory tests to verify they fail.**

  Run: `.venv/bin/python -m pytest --import-mode=importlib tests/store/test_database.py -q`

  Expected: failure because `Database.open_read_only` and `ReadOnlyDatabaseError` do not exist.

- [ ] **Step 3: Implement the factory without sharing write initialization.**

  In `glassbox/store/database.py`, add:

  ```python
  class ReadOnlyDatabaseError(RuntimeError):
      """A database cannot safely serve Glassbox's strict read-only contract."""


  @classmethod
  def open_read_only(cls, path: Path | str, *, busy_timeout_ms: int = 5_000) -> Database:
      if busy_timeout_ms < 0:
          raise ValueError("busy_timeout_ms must be non-negative")
      database_path = Path(path)
      if not database_path.is_file():
          raise ReadOnlyDatabaseError(
              f"Glassbox database does not exist: {database_path}"
          )
      connection: sqlite3.Connection | None = None
      try:
          connection = sqlite3.connect(
              f"{database_path.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False
          )
          connection.row_factory = sqlite3.Row
          connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
          schema_objects = _glassbox_schema_sql(connection)
      except sqlite3.Error as error:
          if connection is not None:
              connection.close()
          raise ReadOnlyDatabaseError(
              "Glassbox database cannot be opened read-only or has an unsupported schema."
          ) from error
      assert connection is not None
      if schema_objects == _strict_schema_objects():
          return cls(connection)
      connection.close()
      if _is_pre_strict_schema(schema_objects):
          raise ReadOnlyDatabaseError(
              "Glassbox database uses the pre-strict schema; run a writer-capable "
              "Glassbox session first to migrate it."
          )
      raise ReadOnlyDatabaseError("Glassbox database has an unsupported schema.")
  ```

  Initialize `connection: sqlite3.Connection | None = None` before the open. Wrap both `sqlite3.connect(...)` and `_glassbox_schema_sql(connection)` in `except sqlite3.Error`; close a successfully opened connection and re-raise `ReadOnlyDatabaseError("Glassbox database cannot be opened read-only or has an unsupported schema.")` without including `str(error)`. Export `ReadOnlyDatabaseError` from `glassbox/store/__init__.py`, and never call `_initialize_schema()` from this method.

- [ ] **Step 4: Add a separate-process WAL regression test.**

  Add a `multiprocessing` test whose writer child opens `Database.open(path)`, writes a strict `TraceEvent`, and remains alive on an `Event`. The parent opens `Database.open_read_only(path)` while the writer connection remains open and asserts the first trace is readable. Signal the child to write a second trace, then poll fresh `Database.open_read_only(path)` connections until the second trace is visible before permitting the child to close.

  Use this synchronization shape:

  ```python
  writer_ready = multiprocessing.Event()
  write_second = multiprocessing.Event()
  writer_done = multiprocessing.Event()
  process = multiprocessing.Process(
      target=_live_wal_writer,
      args=(str(path), writer_ready, write_second, writer_done),
  )
  process.start()
  assert writer_ready.wait(timeout=10)
  # Read trace one through Database.open_read_only while writer is still alive.
  write_second.set()
  # Poll fresh read-only connections until trace two appears, then join.
  assert writer_done.wait(timeout=10)
  process.join(timeout=10)
  assert process.exitcode == 0
  ```

  The child must call `Database.open`, use `Repository.write_event` for valid `TraceEvent` fixtures, and not close its connection until the parent has proved the first uncheckpointed read. This is the target-OS preflight specified by P2.2.

- [ ] **Step 5: Run the full database test module and static checks.**

  Run:

  ```shell
  .venv/bin/python -m pytest --import-mode=importlib tests/store/test_database.py -q
  .venv/bin/ruff check glassbox/store tests/store
  .venv/bin/mypy glassbox/store
  ```

  Expected: all pass, including the separate-process live-WAL case.

- [ ] **Step 6: Commit the isolated read-only contract.**

  ```shell
  git add glassbox/store/database.py glassbox/store/__init__.py tests/store/test_database.py
  git commit -m "feat: add strict read-only database access"
  ```

## Task 2: Typed repository queue, detail, and override reads

**Files:**
- Modify: `glassbox/store/repository.py`
- Modify: `tests/store/test_repository.py`

**Interfaces:**
- Produces `QueueSort = Literal["decided_at", "confidence"]` and `OverrideStatus = Literal["none", "accepted", "modified", "rejected", "inconsistent"]`.
- Produces immutable `QueueCursor(sort_value: str | float, decision_id: str)`, `QueueQuery(...)`, `OverrideRecord(...)`, `QueueDecision(event, override_status, sort_value)`, and `DecisionDetail(stored_decision, overrides)` records. `QueueDecision.sort_value` is the exact selected value read from SQLite.
- Produces `Repository.queue(query: QueueQuery) -> tuple[QueueDecision, ...]`, `Repository.decision_detail(decision_id: str) -> DecisionDetail | None`, and reuses `trace_tree(trace_id)`.

- [ ] **Step 1: Write failing repository tests for filters, fixed sorts, pagination, and override heads.**

  Use three decisions with controlled timestamps/confidence and write raw valid override rows through the test connection. Add assertions like:

  ```python
  def test_queue_uses_only_fixed_sort_columns(repository: Repository) -> None:
      with pytest.raises(ValueError, match="sort column"):
          repository.queue(QueueQuery(sort_column="decided_at; DROP TABLE decisions"))  # type: ignore[arg-type]


  def test_queue_cursor_retains_every_tied_canonical_timestamp(repository: Repository) -> None:
      # Seed two decisions with the identical stored `...Z` timestamp.
      first_page = repository.queue(QueueQuery(limit=1, sort_column="decided_at"))
      cursor = QueueCursor(first_page[0].sort_value, first_page[0].event.decision_id)
      second_page = repository.queue(QueueQuery(limit=10, sort_column="decided_at", cursor=cursor))
      seen = {row.event.decision_id for row in first_page + second_page}
      assert seen == {FIRST_DECISION_ID, SECOND_DECISION_ID}


  def test_effective_override_is_the_unique_unsuperseded_head(repository: Repository) -> None:
      _insert_override(repository, override_id=OVERRIDE_A, action="accepted")
      _insert_override(
          repository,
          override_id=OVERRIDE_B,
          action="modified",
          supersedes_override_id=OVERRIDE_A,
      )
      detail = repository.decision_detail(DECISION_ID)
      assert detail is not None
      assert detail.overrides[-1].override_id == OVERRIDE_B
      assert repository.queue(QueueQuery(override_status="modified"))[0].override_status == "modified"
  ```

  Add rows for: no override (`none`), two independent heads (`inconsistent`), and a self-referencing valid ULID row (`inconsistent`). Assert agent/type/date/confidence bounds are honored and all strings containing SQL punctuation are treated as values, not SQL.

- [ ] **Step 2: Run the focused repository tests to verify missing interfaces fail.**

  Run: `.venv/bin/python -m pytest --import-mode=importlib tests/store/test_repository.py -q`

  Expected: import errors for the new query records/methods or assertion failures.

- [ ] **Step 3: Implement store-owned query records and a fixed SQL builder.**

  Define records near `TraceTree`:

  ```python
  QueueSort = Literal["decided_at", "confidence"]
  OverrideStatus = Literal["none", "accepted", "modified", "rejected", "inconsistent"]

  @dataclass(frozen=True)
  class QueueCursor:
      sort_value: str | float
      decision_id: str

  @dataclass(frozen=True)
  class QueueQuery:
      agent_name: str | None = None
      decision_type: str | None = None
      decided_from: datetime | None = None
      decided_before: datetime | None = None
      confidence_min: float | None = None
      confidence_max: float | None = None
      override_status: OverrideStatus | None = None
      sort_column: QueueSort = "decided_at"
      cursor: QueueCursor | None = None
      limit: int = 25
  ```

  Validate `limit` is `1..100`, confidence bounds are within `0..1` and ordered, and `sort_column` is one of the exact fixed values at runtime. Build predicates from a list of literal fragments and parameter dictionary. Use only this mapping for the interpolated ordering identifier:

  ```python
  _SORT_COLUMNS: dict[QueueSort, str] = {
      "decided_at": "d.decided_at",
      "confidence": "d.confidence",
  }
  ```

  Always append `d.decision_id DESC` as tie-breaker. For a descending cursor, add `(column < :cursor_value OR (column = :cursor_value AND d.decision_id < :cursor_decision_id))`. Populate each `QueueDecision.sort_value` directly from the selected database row: for `decided_at`, retain the schema's canonical `...Z` TEXT exactly; for `confidence`, retain the numeric value. The service must use that field when encoding a cursor, never `event.decided_at.isoformat()`. The caller supplies those stored strings for timestamp cursors and floats for confidence cursors; reject a mismatched type rather than coercing it into SQL.

  Compute override status in a read-only CTE/`CASE`: no rows is `none`; one override row with no child is its `action`; any other nonzero head count is `inconsistent`. Fetch all override rows for `decision_detail` ordered by `created_at, override_id`; do not select an arbitrary current row for malformed history.

- [ ] **Step 4: Run focused repository tests and quality checks.**

  Run:

  ```shell
  .venv/bin/python -m pytest --import-mode=importlib tests/store/test_repository.py -q
  .venv/bin/ruff check glassbox/store tests/store
  .venv/bin/mypy glassbox/store
  ```

  Expected: all pass; injection-shaped filter/sort values never alter schema or result shape.

- [ ] **Step 5: Commit typed read queries.**

  ```shell
  git add glassbox/store/repository.py tests/store/test_repository.py
  git commit -m "feat: add typed decision read queries"
  ```

## Task 3: Web presentation models and read service

**Files:**
- Create: `glassbox/web/read_models.py`
- Create: `glassbox/web/read_service.py`
- Create: `tests/web/test_read_models.py`
- Create: `tests/web/test_read_service.py`

**Interfaces:**
- Produces `ConfidenceBand(str, Enum)` with `LOW`, `MEDIUM`, `HIGH`; `confidence_band(confidence: float) -> ConfidenceBand`; and `confidence_bounds(band: ConfidenceBand) -> tuple[float, float | None]`.
- Produces `QueuePage`, `QueueRow(sort_value: str | float, ...)`, `DecisionCard`, `EvidenceGroupView`, `CitationView`, `OverrideView`, `TraceView`, `SpanView`, and `Diagnostic` frozen models.
- Produces `decode_cursor(value: str | None, sort_column: QueueSort) -> QueueCursor | None`, `encode_cursor(row: QueueRow, sort_column: QueueSort) -> str`, and `ReadService(database_path: Path)` methods `queue(...)`, `decision_card(decision_id)`, `trace(trace_id)`.

- [ ] **Step 1: Write failing pure-model tests.**

  Create `tests/web/test_read_models.py` with boundary and grouping coverage:

  ```python
  @pytest.mark.parametrize(
      ("confidence", "expected"),
      [(0.0, ConfidenceBand.LOW), (0.499999, ConfidenceBand.LOW),
       (0.5, ConfidenceBand.MEDIUM), (0.799999, ConfidenceBand.MEDIUM),
       (0.8, ConfidenceBand.HIGH), (1.0, ConfidenceBand.HIGH)],
  )
  def test_confidence_band_boundaries(confidence: float, expected: ConfidenceBand) -> None:
      assert confidence_band(confidence) is expected


  def test_recommendation_summary_is_canonical_and_truncated() -> None:
      summary = recommendation_summary({"z": "x" * 200, "a": 1}, limit=24)
      assert summary.startswith('{"a":1,"z":')
      assert summary.endswith("…")


  def test_citation_groups_all_evidence_fields_and_reports_dangling() -> None:
      card = build_decision_card(_stored_decision_with_two_inventory_fields(), ())
      assert [group.evidence_id for group in card.evidence_groups] == ["inventory"]
      assert len(card.evidence_groups[0].fields) == 2
      assert card.citations[1].diagnostic == "Unresolved evidence citation: demand"
      assert card.citations[0].anchor == "evidence-0"
  ```

  Add tests that a normal override chain yields its head, while independent heads and a self-reference yield `OverrideView(status="inconsistent")`; add span fixtures where a missing parent creates an orphan diagnostic and where two present spans mutually name each other as parents. The latter must produce a cycle diagnostic and no cyclic `SpanView.children` graph.

- [ ] **Step 2: Run model tests to verify the missing module fails.**

  Run: `.venv/bin/python -m pytest --import-mode=importlib tests/web/test_read_models.py -q`

  Expected: `ModuleNotFoundError: No module named 'glassbox.web.read_models'`.

- [ ] **Step 3: Implement immutable web models and conversion helpers.**

  Keep all models in `read_models.py` frozen dataclasses. Implement recommendation formatting through existing `canonical_dumps`, truncate after a character count without breaking escaped template rendering, and format a Decision Card verdict as `"{summary} — High (0.80)"`.

  `build_decision_card()` must group stored evidence by caller-defined `evidence_id` in repository order, retain every field, and create positional anchors `evidence-0`, `evidence-1`, not caller text. Preserve citation order and use a `Diagnostic` for each missing group. Build span roots/children by `parent_span_id`, order siblings by `(started_at, span_id)`, and retain malformed-parent spans as orphan rows with a diagnostic. Track an active ancestry set while placing spans; if adding a present-parent relationship closes a cycle, emit its members as diagnostic/orphan rows and never place the cyclic edge into `SpanView.children`.

- [ ] **Step 4: Write failing service tests for per-call connections and request parsing.**

  Create `tests/web/test_read_service.py`:

  ```python
  def test_read_service_opens_fresh_read_only_database_per_call(
      tmp_path: Path, monkeypatch: pytest.MonkeyPatch
  ) -> None:
      path = _strict_database_with_decision(tmp_path)
      service = ReadService(path)
      calls: list[Path] = []
      real_open = Database.open_read_only

      def recording_open(path: Path | str, **kwargs: object) -> Database:
          calls.append(Path(path))
          return real_open(path, **kwargs)

      monkeypatch.setattr(Database, "open_read_only", recording_open)
      assert service.queue(QueueRequest()).rows
      assert service.queue(QueueRequest()).rows
      assert calls == [path, path]


  def test_decode_cursor_rejects_unknown_keys_and_wrong_sort_value_type() -> None:
      with pytest.raises(ValueError, match="cursor"):
          decode_cursor(_encode({"value": "not-a-float", "decision_id": DECISION_ID}), "confidence")
  ```

  Add service tests mapping `from`/`to` `YYYY-MM-DD` values to UTC `[start, next-midnight)` bounds, mapping only `timestamp`/`confidence` aliases, and returning `None` for missing card/trace data. For each seeded normal, branched, and self-referencing override history, assert the queue row's `override_status` equals `ReadService.decision_card(decision_id).override.status`; this cross-layer regression test prevents the SQL classification and card derivation from drifting.

- [ ] **Step 5: Implement the service and run all web-model/service checks.**

  `ReadService` must create `Database.open_read_only(self._database_path)` inside each public method, wrap it in `try/finally`, construct `Repository(database)`, and close it before returning. It must never retain the database, connection, or repository on `self`.

  Parse public query values into a `QueueRequest` in the service: exact strings for agent/type; dates only through `date.fromisoformat`; confidence only through `ConfidenceBand`; and sort aliases only through `{"timestamp": "decided_at", "confidence": "confidence"}`. Encode cursors as unpadded base64url JSON exactly `{"decision_id": str, "value": str|float}`, sourcing `value` from `QueueRow.sort_value` passed through from `QueueDecision.sort_value`; restore required padding before decoding and reject any other keys.

  Run:

  ```shell
  .venv/bin/python -m pytest --import-mode=importlib tests/web/test_read_models.py tests/web/test_read_service.py -q
  .venv/bin/ruff check glassbox/web tests/web
  .venv/bin/mypy glassbox/web
  ```

- [ ] **Step 6: Commit presentation conversion.**

  ```shell
  git add glassbox/web/read_models.py glassbox/web/read_service.py tests/web/test_read_models.py tests/web/test_read_service.py
  git commit -m "feat: add planner read models"
  ```

## Task 4: Authenticated read routes and escaped templates

**Files:**
- Modify: `glassbox/web/server.py`
- Create: `glassbox/web/templates/base.html`
- Create: `glassbox/web/templates/queue.html`
- Create: `glassbox/web/templates/decision_card.html`
- Create: `glassbox/web/templates/trace.html`
- Create: `glassbox/web/templates/error.html`
- Create: `glassbox/web/static/glassbox.css`
- Modify: `tests/web/test_server.py`

**Interfaces:**
- `ServerConfig` gains `database_path: Path`; `load_server_config()` reads `GLASSBOX_DATABASE` or `glassbox.sqlite3`.
- `create_app()` preflights and closes `Database.open_read_only(config.database_path)` before returning FastAPI.
- Authenticated routes call a short-lived `ReadService(config.database_path)` per request.

- [ ] **Step 1: Write failing server route tests using a strict seeded database.**

  First update the existing `app_for` helper in `tests/web/test_server.py` so it creates and closes a strict temporary database, passes that path through `GLASSBOX_DATABASE`, and uses it in `ServerConfig`. This preserves P2.1's auth-focused tests after `create_app()` starts read-only schema preflight. Give every route test an explicit seeded database path rather than relying on the process working directory.

  Extend `tests/web/test_server.py` with a fixture that creates `Database.open(tmp_path / "glassbox.sqlite3")`, persists a trace, span, decision, and multi-field evidence, then closes it. Configure `GLASSBOX_DATABASE` to that path and authenticate through the existing login helper. Add:

  ```python
  def test_queue_renders_escaped_decisions_and_empty_state() -> None:
      client = authenticated_client(seed_database_with(rationale="<script>alert(1)</script>"))
      response = client.get("/")
      assert response.status_code == 200
      assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
      assert "<script>alert(1)</script>" not in response.text


  def test_card_groups_evidence_and_marks_unresolved_citation() -> None:
      response = authenticated_client(seed_database_with(dangling_citation=True)).get(
          f"/decision/{DECISION_ID}"
      )
      assert response.status_code == 200
      assert "inventory" in response.text
      assert "Unresolved evidence citation: demand" in response.text


  def test_missing_card_and_trace_are_generic_not_found() -> None:
      client = authenticated_client(seed_database())
      assert client.get("/decision/01ARZ3NDEKTSV4RRFFQ69G5FAA").status_code == 404
      assert client.get("/trace/01ARZ3NDEKTSV4RRFFQ69G5FAB").status_code == 404
  ```

  Add assertions that an unconfigured/missing `GLASSBOX_DATABASE` makes `create_app()` fail before an app is returned; a runtime `ReadOnlyDatabaseError` renders a generic `503` without the database path/error text; an unauthenticated queue remains a redirect to `/login`; and trace output includes span/blob references but not blob content.

- [ ] **Step 2: Run route tests to verify P2.1’s root placeholder fails the new expectations.**

  Run: `.venv/bin/python -m pytest --import-mode=importlib tests/web/test_server.py -q`

  Expected: the existing authenticated root `503` and missing decision/trace routes fail the read-experience assertions.

- [ ] **Step 3: Replace the placeholder with preflighted read routes.**

  In `load_server_config`, add:

  ```python
  database_path = Path(source.get("GLASSBOX_DATABASE", "glassbox.sqlite3"))
  return ServerConfig(host, port, token, session_idle_timeout, database_path)
  ```

  Add `database_path: Path` to `ServerConfig`. At the top of `create_app`, call `Database.open_read_only(config.database_path).close()` before constructing the app. Mount `StaticFiles` at `/static`, pass only page models to `TemplateResponse`, and replace the P2.1 root endpoint with the queue route. Catch only `ReadOnlyDatabaseError` and `sqlite3.Error` around service calls; render `error.html` at `503` with fixed title/body text, never `str(exc)`.

  Implement the templates with Jinja inheritance from `base.html`. The queue's GET form uses `agent`, `decision_type`, `from`, `to`, `confidence`, `override_status`, `sort`, and `cursor`. It renders an explicit “No decisions match these filters.” state. The card renders a `<table>` of each evidence group, anchor IDs from `CitationView.anchor`, unresolved diagnostics, alternatives, and a non-submit feedback-deferred notice. The trace recursively renders only the cycle-free `SpanView.children` tree and renders orphan/cycle diagnostics as text. `glassbox.css` contains only local, readable typography/table/layout rules and no external URL or script.

- [ ] **Step 4: Run route, quality, and architecture checks.**

  Run:

  ```shell
  .venv/bin/python -m pytest --import-mode=importlib tests/web/test_server.py tests/web/test_read_models.py tests/web/test_read_service.py -q
  .venv/bin/ruff check glassbox tests
  .venv/bin/mypy glassbox
  .venv/bin/lint-imports
  ```

  Expected: all pass and import-linter reports six kept contracts.

- [ ] **Step 5: Commit the P2.2 read surface.**

  ```shell
  git add glassbox/web/server.py glassbox/web/templates glassbox/web/static tests/web/test_server.py
  git commit -m "feat: add planner read experience"
  ```

## Task 5: Documentation, P2.2 evidence, and final verification

**Files:**
- Modify: `README.md`
- Modify: `TODO.md`

**Interfaces:**
- Documents the required existing Glassbox database and the authenticated queue/card/trace URLs.
- Does not claim feedback submission, static export, prompt/completion display, or remote serving.

- [ ] **Step 1: Update the README with P2.2's actual capabilities.**

  Add a short paragraph to `## Run the local planner server`:

  ```markdown
  Set `GLASSBOX_DATABASE` when the database is not `glassbox.sqlite3` in the
  current directory. The server verifies that an existing database has the
  current schema before it binds. After login, `/` lists decisions,
  `/decision/<id>` shows a Decision Card, and `/trace/<id>` shows trace timing
  data. This phase is read-only: feedback and static export are not available.
  ```

- [ ] **Step 2: Mark only P2.2 complete in `TODO.md`.**

  Replace its checkbox with:

  ```markdown
  - [x] Build queue, Decision Card, and trace-view models and templates.
  ```

  Do not alter P2.3 or P2.4 status.

- [ ] **Step 3: Run the full verification suite.**

  Run:

  ```shell
  .venv/bin/python -m pytest --import-mode=importlib -q
  .venv/bin/ruff check .
  .venv/bin/mypy glassbox
  .venv/bin/lint-imports
  GLASSBOX_LOCAL_ACCESS_TOKEN='local-smoke-token-1234567890123456' GLASSBOX_DATABASE=/path/to/strict.sqlite3 .venv/bin/glassbox serve --host 0.0.0.0 --port 8787
  ```

  Expected: all quality commands pass; `lint-imports` reports six kept contracts; the final command exits `2` before binding and mentions loopback-only hosting. Then seed a temporary strict database, start the server on loopback, authenticate, and verify `/`, one card, and one trace return `200` before stopping the server.

- [ ] **Step 4: Commit documentation and completion evidence.**

  ```shell
  git add README.md TODO.md
  git commit -m "docs: describe planner read experience"
  ```

## Self-review

- **Spec coverage:** Task 1 implements read-only strict-schema access, normalized SQLite failures, and target-OS WAL proof. Task 2 implements bound filters, fixed sorts, stable cursors based on stored canonical values, queue pages, and effective override classification. Task 3 implements one-source confidence, generic JSON display, evidence groups, dangling citations, override cross-checking, cycle-safe span diagnostics, and per-request connections. Task 4 implements all authenticated read routes/templates, escaping, 404/503 behavior, and no blob content. Task 5 records operator guidance, P2.2 completion, and final evidence.
- **Placeholder scan:** Every task specifies its files, interfaces, red test, command, expected outcome, implementation behavior, and commit. Deferred P2.3/P2.4 functionality is deliberately excluded rather than represented as a stub.
- **Type consistency:** `Database.open_read_only`, `ReadOnlyDatabaseError`, `QueueQuery`, `QueueCursor`, `QueueSort`, `OverrideStatus`, `ReadService`, `ConfidenceBand`, `QueuePage`, `DecisionCard`, `TraceView`, and `ServerConfig.database_path` are introduced before consuming tasks with the same names and roles.
