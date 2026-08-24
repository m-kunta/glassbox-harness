# Task 4 report — bounded fail-open collector

## Status

Completed P0.4 in `/private/tmp/glassbox-p0-trace-foundation`.

## Delivered

- `EventBuffer`, a bounded FIFO queue whose producer path uses non-blocking
  admission and drops the newest event when full.
- `Collector`, a single background writer consuming canonical trace, span,
  decision, and evidence events through the repository boundary.
- Public fail-open methods: `emit(event) -> bool`, `flush(timeout) -> bool`,
  and `shutdown(timeout) -> bool`. They contain queue, timeout, and repository
  failures rather than raising into agent code.
- Queue-drop metrics (`dropped_events` and `dropped_by_trace`) and a separate
  repository-failure metric (`failed_events`). Warnings share a configurable
  rate limit.
- Partial trace handling: loss before a closing trace write changes that
  closing event to `status="partial"`; loss after the closing write preserves
  the stored status and leaves the counter and warning as authoritative.
- A collector import-linter contract, now that the package exists.
- A repository connection configured for the required background writer thread,
  verified by a real SQLite collector test.

## TDD evidence

1. Added the buffer and writer tests before creating the collector package.
2. Initial RED command:

   ```text
   .venv/bin/python -m pytest tests/collector -v
   ```

   Result: collection failed as expected with
   `ModuleNotFoundError: No module named 'glassbox.collector'`.
3. Implemented the minimal queue and writer; the focused behavior suite passed.
4. Added a real `Repository` integration regression before changing SQLite
   connection configuration. Its RED command was:

   ```text
   .venv/bin/python -m pytest \
     tests/collector/test_writer.py::test_collector_persists_through_the_real_repository -v
   ```

   Result: `failed_events == 2`, because SQLite rejected use of its default
   thread-affine connection in the collector worker.
5. Enabled `check_same_thread=False` in the database connection factory. The
   real repository test and the complete collector suite then passed.
6. Updated the architecture contract test first, observed the expected missing
   `collector-dependencies` configuration failure, then activated the contract.

## Verification

All commands used the project `.venv/bin/python`.

```text
.venv/bin/python -m pytest tests/collector -v  9 passed
.venv/bin/python -m pytest -q                 118 passed
.venv/bin/python -m ruff check .               All checks passed
.venv/bin/python -m mypy glassbox              Success: no issues found in 12 source files
.venv/bin/lint-imports                         3 contracts kept, 0 broken
git diff --check                               passed
```

## Scope and concern

The requested collector surface is complete and uses one worker thread. The
small `Database` change is necessary rather than incidental: a repository
created by the existing `Database.open()` otherwise fails every worker-thread
write. The collector still serializes its own writes; callers should avoid
unsynchronized concurrent use of the same repository connection outside the
collector worker.

## Review remediation TDD evidence

1. Added four behavioral regressions before changing production code:
   linearized emit/shutdown admission, repository transaction coordination,
   final-write partial marking, and the timed-out shutdown worker policy.
2. RED command:

   ```text
   .venv/bin/python -m pytest tests/collector/test_writer.py \
     -k 'in_progress_emit or serializes_foreground or final_repository_loss or timed_out_shutdown' -v
   ```

   Result: 4 failed as expected. The worker exited during a paused enqueue;
   `mark_trace_partial` finished during the worker transaction; final write
   loss did not produce a partial update; and the timed-out worker was
   non-daemon.
3. GREEN command:

   ```text
   .venv/bin/python -m pytest tests/collector/test_writer.py \
     -k 'in_progress_emit or serializes_foreground or final_repository_loss or timed_out_shutdown' -v
   ```

   Result: 4 passed. The complete collector suite then passed (13 tests), as
   did the store suite (76 tests).
4. Final verification:

   ```text
   .venv/bin/python -m pytest -q       122 passed
   .venv/bin/python -m ruff check .    All checks passed
   .venv/bin/python -m mypy glassbox   Success: no issues found in 12 source files
   .venv/bin/lint-imports              3 contracts kept, 0 broken
   git diff --check                    passed
   ```
