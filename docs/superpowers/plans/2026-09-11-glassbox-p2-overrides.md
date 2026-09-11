# Glassbox P2.5 Operational Overrides Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add secure, append-only accept/modify/reject actions that create one linear operational override history per decision.

**Architecture:** `Repository.record_override()` owns immediate-transaction head discovery, idempotent replay, and insertion. The loopback web server validates a form then calls the repository through one writer connection; read models continue to display the persisted history.

**Tech Stack:** Python 3.11+, SQLite WAL, FastAPI/Jinja2, pytest, Ruff, mypy.

## Global Constraints

- `GLASSBOX_OPERATOR_NAME` defaults to `local-planner`; configured values are stripped and must be non-empty.
- Only `modified` accepts required corrected-recommendation JSON; `accepted`/`rejected` reject it.
- The writer uses `BEGIN IMMEDIATE`; empty history is valid, existing zero/multiple-head history is rejected without a write.
- Idempotency compares only decision/action/modified-value/reason/free-text, never generated ID or timestamp.
- POST retains P2.3 Host, Origin, CSRF, session-refresh, generic-error, and PRG behavior.

## Task 1: Atomic override repository API

**Files:** `glassbox/store/repository.py`, `tests/store/test_repository.py`, `tests/web/test_read_service.py`

- [ ] Write failing tests for a first row, linear successor, exact replay built with a distinct generated ID/timestamp, changed-payload rejection, and refusal on self-reference/branching.
- [ ] Add frozen `OverrideSubmission` and `Repository.record_override(submission) -> OverrideRecord`. Inside the database operation lock, execute `BEGIN IMMEDIATE`, load decision rows, classify heads, return exact key replay or insert with the chosen predecessor, then commit/rollback.
- [ ] Extend the existing queue/card agreement test: after a repository successor write, assert queue SQL status/current action and Decision Card graph-walk action agree with the writer-selected head.
- [ ] Run `pytest --import-mode=importlib tests/store/test_repository.py tests/web/test_read_service.py -q`, Ruff, and mypy; commit `feat: add atomic override repository writes`.

## Task 2: Secure operator override form

**Files:** `glassbox/web/server.py`, `glassbox/web/templates/decision_card.html`, `tests/web/test_server.py`

- [ ] Write failing HTTP tests for operator-name default/trim/rejection, first accept, modified JSON requirement, reject/accept JSON rejection, successor chain, Host/Origin/CSRF rejection, and PRG.
- [ ] Add `operator_name` to `ServerConfig`; read/strip `GLASSBOX_OPERATOR_NAME` in `load_server_config` and reject explicitly configured blank values.
- [ ] Render an override form with its own opaque idempotency key. Add POST `/decision/{decision_id}/override`, reuse P2.3 transport guards and limits, construct an `OverrideSubmission`, call the repository through a short-lived writer, map inconsistent/idempotency/input failures to generic 400 and busy errors to generic retryable 503, then redirect 303.
- [ ] Render current override/history through existing `card.override`; autoescape stored values.
- [ ] Run focused web tests, then full pytest/Ruff/mypy/import-linter; commit `feat: add secure operational overrides`.

## Task 3: Completion evidence

**Files:** `README.md`, `TODO.md`, `docs/p2-planner-usability.md`

- [ ] Document `GLASSBOX_OPERATOR_NAME`, override action semantics, and append-only supersession.
- [ ] Mark P2.5 complete only after automated checks pass. Add accept, modify, reject, supersession, and duplicate-delivery rows to the planner checklist; do not mark the usability session complete without a real participant.
- [ ] Run the full quality gate and commit `docs: complete operational override workflow`.
