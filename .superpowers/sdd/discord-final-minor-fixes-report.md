# Discord final Minor findings

## Changes

- Recovery orders runs by `glassbox_timestamp_key(as_of)` descending, preserving
  the descending drift-run ID tie-breaker and existing 100-run/three-attempt caps.
- Added a real SQLite regression with seconds, milliseconds, and microseconds
  timestamps, equal-time run IDs, and 99 newer unattempted runs. It proves both
  newest-three selection and that the correctly newest candidate occupies the
  last slot in the capped 100-run set.
- README now states two requests per delivery attempt, recovery only after a
  newly persisted detected report, the 100-run inspection/three-prior-attempt
  limits, and up to eight requests/four capped waits per qualifying invocation.
  Five assertions extend the existing README operations contract test.

## Red/green evidence

- RED, before production/document edits:
  `.venv/bin/pytest -q tests/eval/test_discord.py -k mixed_precision tests/test_architecture.py -k 'mixed_precision or readme_documents_discord_drift_alert_operations'`
  produced **7 failed, 24 passed, 153 deselected**. Both timestamp cases selected
  the older whole-second run first (ID suffix C8 instead of CB); the five new
  README contracts failed because the precise limits were absent.
- GREEN:
  `.venv/bin/pytest -q tests/eval/test_discord.py tests/test_architecture.py tests/store/test_repository.py`
  produced **305 passed in 3.67 seconds**. This run used approved execution with
  loopback binding because the sandbox initially blocked the existing HTTP
  fixtures with `PermissionError` (36 blocked fixture tests, 148 passed).
- `.venv/bin/ruff check glassbox/store/repository.py tests/eval/test_discord.py tests/test_architecture.py`:
  **All checks passed**.
- `git diff --check`: clean.

## Concerns

No outstanding concerns for these findings. No live Discord requests or secrets
were used. Delivery/retry runtime behavior is unchanged apart from chronological
candidate selection. The pre-existing untracked `.superpowers/brainstorm/`
directory is outside this change. Nothing was pushed.
