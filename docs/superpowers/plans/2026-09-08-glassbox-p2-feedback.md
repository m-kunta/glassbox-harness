# Glassbox P2.3 Feedback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair the P2.2 HTTP read surface and add secure, append-only planner feedback.

**Architecture:** Repair templates and request validation at the HTTP boundary first. Add a `feedback` ledger to the strict SQLite schema and repository, then add a guarded POST route which opens one short-lived writer connection, persists atomically, and redirects back to the card.

**Tech Stack:** Python 3.11+, SQLite WAL, FastAPI, Jinja2, pytest, Ruff, mypy.

## Global constraints

- Feedback is separate from `overrides`; P2.5 owns operational override writes.
- `feedback` is append-only and idempotent only for exact same-form/key replay.
- POST requires exact loopback Host; Origin may be absent but must match when present; CSRF is constant-time.
- A busy write timeout returns a generic retryable failure and persists nothing.
- `web` may import `store`, never `sdk`; persisted text is autoescaped.

## Task 1: Repair P2.2 route coverage

**Files:** `glassbox/web/templates/trace.html`, `glassbox/web/server.py`, `tests/web/test_server.py`, `tests/web/test_read_service.py`, `TODO.md`.

- [ ] Add failing HTTP tests seeded with trace/span/decision/evidence that assert `/decision/<id>` and `/trace/<id>` return 200; assert malformed `sort`, `from`, `confidence`, and `cursor` return 400 without exception text; assert missing IDs return generic 404.
- [ ] Add a failing cross-layer test that seeds normal, branched, and self-referencing override histories and asserts each queue row status equals `ReadService.decision_card(id).override.status`.
- [ ] Run `pytest --import-mode=importlib tests/web/test_server.py tests/web/test_read_service.py -q`; expect trace template failure and raw 500 validation failures.
- [ ] Remove the extra `{% endfor %}` from `trace.html`. Catch `ValueError` in the queue route and render a generic 400 error template; do not catch it as a 503 database failure.
- [ ] Run focused tests, `ruff check glassbox tests`, `mypy glassbox`, and `lint-imports`; commit `fix: harden planner read routes`.

## Task 2: Append-only feedback storage

**Files:** `glassbox/store/migrations/002_feedback.sql`, `glassbox/store/schema.sql`, `glassbox/store/database.py`, `glassbox/store/repository.py`, `glassbox/store/__init__.py`, `tests/store/test_schema.py`, `tests/store/test_repository.py`.

- [ ] Write failing tests for strict migration from the released schema; valid feedback write/read; FK rejection for a missing decision; same-key/same-payload replay returning the original row; same key with changed payload rejecting; and two keys creating two rows.
- [ ] Run focused store tests; expect missing feedback interfaces/migration failures.
- [ ] Add `feedback(feedback_id TEXT PRIMARY KEY, decision_id FK RESTRICT, verdict CHECK, reason_code, free_text, corrected_recommendation JSON, created_at strict UTC, idempotency_key UNIQUE)` through migration 002. Update `Database.open()` to apply 002 only after the current strict fingerprint; update `open_read_only()` to accept the released feedback schema. Keep `schema.sql` byte-identical to the latest schema migration.
- [ ] Add frozen `FeedbackRecord`, `FeedbackSubmission`, and `Repository.record_feedback(submission) -> FeedbackRecord` plus `Repository.feedback_for_decision(id)`. Generate feedback ULIDs at the web/service boundary; compare a duplicate key's immutable fields before returning its original record.
- [ ] Run focused store tests, full schema drift test, Ruff, mypy; commit `feat: add append-only feedback ledger`.

## Task 3: Secure feedback form and POST

**Files:** `glassbox/web/read_models.py`, `glassbox/web/read_service.py`, `glassbox/web/server.py`, `glassbox/web/templates/decision_card.html`, `glassbox/web/templates/error.html`, `tests/web/test_server.py`, `tests/web/test_read_service.py`.

- [ ] Write failing tests for a rendered CSRF/idempotency form; valid PRG feedback POST; absent/mismatched Host; mismatched Origin; absent Origin; invalid CSRF/verdict/JSON/oversized note; database busy error yielding generic retryable response; escaped newest-first history; and authenticated failed POST refreshing the session.
- [ ] Run the route tests; expect no POST route/form/history.
- [ ] Add `FeedbackView` to the card model and render feedback newest-first. Generate a fresh key per GET form. Parse request limits explicitly: verdict max 16 chars, reason code max 64, free text max 4 KiB, corrected recommendation max 16 KiB. Use `secrets.compare_digest` for CSRF; validate Host against configured host/port and Origin only when supplied. On POST, open `Database.open(config.database_path)`, call the repository transaction, close in `finally`, then redirect `303` to the card. Catch `sqlite3.OperationalError` as generic retryable 503 only after the transaction rolled back.
- [ ] Run all web tests, `ruff check .`, `mypy glassbox`, and `lint-imports`; commit `feat: add secure planner feedback`.

## Task 4: Completion evidence

**Files:** `README.md`, `TODO.md`.

- [ ] Document feedback's separate-ledger role and P2.5 operational overrides; mark the P2.2 remediation complete and P2.3 complete only after all tests pass.
- [ ] Run `.venv/bin/python -m pytest --import-mode=importlib -q`, `.venv/bin/ruff check .`, `.venv/bin/mypy glassbox`, and `.venv/bin/lint-imports`; commit `docs: complete planner feedback workflow`.

## Self-review

- Task 1 closes every confirmed P2.2 route and coverage gap.
- Task 2 supplies schema, migration, idempotency, and append-only persistence.
- Task 3 implements Host/Origin/CSRF/request-limit/PRG/write-contention requirements.
- Task 4 records only verified completion.
