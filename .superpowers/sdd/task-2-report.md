# Task 2 report — SQLite event store

## Status

Completed P0.2 in `/private/tmp/glassbox-p0-trace-foundation`.

## Delivered

- A complete initial SQLite schema for `traces`, `spans`, `decisions`, `evidence`,
  `overrides`, `outcomes`, `eval_runs`, and `eval_results`.
- An idempotent `Database.open(path, busy_timeout_ms=...)` connection factory.
  Every opened connection enables foreign keys, WAL, and its configured busy timeout.
- Typed `Repository.write_event`, `mark_trace_partial`, and `trace_tree` APIs.
- Readback reconstructs the immutable event contracts and preserves JSON scalar,
  object, list, Boolean, and null types.
- A decision-scoped composite primary key for `(decision_id, evidence_id)`, so
  caller-defined citation keys reject duplicates without global ULID semantics.
- Cascade deletion for unlabelled traces, with `ON DELETE RESTRICT` for decisions
  that have overrides or outcomes.
- Store-boundary constraints for canonical system ULIDs, valid RFC 3339 UTC values,
  JSON fields, trace status, span kind, confidence, override action, outcome label,
  and evaluation pass values.

## TDD record

1. Added the schema and repository tests before creating `glassbox.store`.
2. RED command:

   ```shell
   .venv/bin/python -m pytest tests/store -v
   ```

   Result: collection failed as expected with
   `ModuleNotFoundError: No module named 'glassbox.store'`.
3. Implemented the migration, connection factory, writes, partial update, and
   trace-tree hydration. The focused suite then passed: `12 passed`.
4. Added the missing store-boundary ULID regression. Its RED command was:

   ```shell
   .venv/bin/python -m pytest tests/store/test_schema.py::test_schema_rejects_non_ulid_system_identifiers -v
   ```

   Result: `Failed: DID NOT RAISE IntegrityError`.
5. Added canonical ULID checks to all system-generated identifiers in both schema
   copies. The focused suite then passed: `13 passed`.
6. Added the malformed-date regression to prevent a syntactically `Z`-suffixed,
   but invalid, timestamp. Its RED command was:

   ```shell
   .venv/bin/python -m pytest tests/store/test_schema.py::test_schema_enforces_status_confidence_and_utc_timestamp_constraints -v
   ```

   Result: `Failed: DID NOT RAISE IntegrityError`.
7. Added SQLite `strftime` validity checks alongside the RFC 3339 `Z` form.
   The focused suite again passed: `13 passed`.

## Verification

All checks used the project `.venv/bin/python` (Python 3.12.13).

```text
.venv/bin/python -m pytest tests/store -v  13 passed
.venv/bin/python -m pytest -q              42 passed
.venv/bin/ruff check .                     All checks passed
.venv/bin/mypy glassbox                    Success: no issues found in 6 source files
.venv/bin/lint-imports                     Contracts: 0 kept, 0 broken
git diff --check                           passed
cmp -s glassbox/store/schema.sql glassbox/store/migrations/001_initial.sql
                                           passed
```

## Scope and self-review

- No P0.3+ runtime modules were added.
- `overrides`, `outcomes`, and evaluation tables are schema-only; their write APIs
  remain intentionally deferred to their owning phases.
- The schema's complete P0 surface is present even where corresponding models or
  ingest code are deferred.
- The partial-trace API does not create an orphan trace when a trace was never
  successfully persisted.
- The migration and reference schema are intentionally identical so they cannot
  diverge during this single-migration phase.
