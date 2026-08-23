# Task 1 report — package foundation and architectural contracts

## Status

Completed P0.1 in `/private/tmp/glassbox-p0-trace-foundation`.

## Delivered

- Hatchling package metadata, development-tool configuration, README, and ignore rules.
- Immutable, dependency-neutral Pydantic contracts for `TraceEvent`, `SpanEvent`, `DecisionEvent`, and `EvidenceEvent`.
- UTC-only timestamp validation and RFC 3339 `Z` serialization.
- ULID validation for system-generated trace, span, decision, and parent-span identifiers.
- Deterministic sorted-key JSON via `canonical_json()`.
- Import-linter contracts for the approved events, SDK, collector, store, and web module boundaries.
- Tests covering canonical JSON, immutability, timestamps, ULIDs, confidence bounds, SDK-compatible evidence citation keys, and architecture configuration.

## TDD record

1. Created the contract tests before package code.
2. RED command:

   ```shell
   .venv/bin/python -m pytest tests/events/test_models.py tests/test_architecture.py -v
   ```

   Result: collection failed as expected with `ImportError: cannot import name 'DecisionEvent' from 'glassbox.events'`; the package contracts did not yet exist.
3. Added the minimal package metadata, exports, models, and import-linter configuration.
4. Added the missing collector boundary assertion first, observed its expected architecture-test failure, then added the matching import-linter contract.
5. GREEN command:

   ```shell
   .venv/bin/python -m pytest tests/events/test_models.py tests/test_architecture.py -v
   ```

   Result: `13 passed`.

## Verification

All commands used the project-local Python 3.12 virtual environment created from `/Users/MKunta/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3`.

```text
.venv/bin/python -m pytest tests/events/test_models.py tests/test_architecture.py -v  13 passed
.venv/bin/ruff check .                                                       All checks passed
.venv/bin/mypy glassbox                                                      Success: no issues found in 3 source files
.venv/bin/lint-imports                                                       Contracts: 0 kept, 0 broken
git diff --check                                                              passed
```

`lint-imports` sees only the three Task 1 source files; contracts for later P0 modules are configured but become active as those modules are created.

## Decision recorded

The approved SDK example requires literal evidence citation keys such as `inventory_position`, while the evidence schema scopes uniqueness within a decision. The project owner resolved the conflict: `EvidenceEvent.evidence_id` is a non-empty caller-defined citation key; system-generated IDs remain ULIDs. The decision is recorded in `TODO.md` and the P0 plan.

## Self-review

- No P1/P2 modules were created.
- Event models only import the standard library and Pydantic.
- The package root exposes only canonical event contracts in this task; public tracing SDK exports remain for Task 5.
- The supplied TODO and plan refinements are included with the task commit as requested.

## Review-fix report

### Findings resolved

1. `glassbox/events/__init__.py` now uses the relative import `from .models import ...`.
   An AST-based behavioral guard scans each Python source file in `glassbox/events/`
   and fails on absolute `import glassbox...` or `from glassbox... import ...` syntax.
2. Canonical mapping and sequence payload data is recursively frozen during Pydantic
   validation. `FrozenDict` is read-only; nested lists and tuples become tuples.
   JSON serializers make immutable payloads JSON-compatible without changing
   deterministic `canonical_json()` output.

### Fresh TDD evidence

RED, after adding review tests and before production changes:

```shell
.venv/bin/python -m pytest tests/events/test_models.py tests/test_architecture.py -v
```

Result: `4 failed, 13 passed`: three nested-mutation tests accepted writes and the
architecture test found `from glassbox.events.models import ...`.

GREEN, after the relative import and recursive freezing/serialization fixes:

```shell
.venv/bin/python -m pytest tests/events/test_models.py tests/test_architecture.py -v
```

Result: `17 passed`.

### Focused verification

- Event and architecture tests: `17 passed`
- `ruff check .`: passed
- `mypy glassbox`: `Success: no issues found in 3 source files`
- `lint-imports`: `Contracts: 0 kept, 0 broken`
- `git diff --check`: passed

## Second review-fix report

### Findings resolved

1. `FrozenDict` now keeps its internal values in a `MappingProxyType`; accessing
   `event.attributes._values` exposes a read-only proxy rather than a mutable
   dictionary. Recursive freezing and JSON serialization behavior are unchanged.
2. The absolute-import architecture check now searches `glassbox/events/`
   recursively. A fixture-style test proves an absolute import in a nested event
   module is reported.

### Fresh TDD evidence

RED, after adding the backing-store regression test and before changing
`FrozenDict`:

```shell
.venv/bin/python -m pytest tests/events/test_models.py tests/test_architecture.py -v
```

Result: `1 failed, 18 passed`. The new test showed
`event.attributes._values["inventory"] = 5` mutated the event payload.

GREEN, after storing the values behind a read-only mapping proxy:

```shell
.venv/bin/python -m pytest tests/events/test_models.py tests/test_architecture.py -v
```

Result: `19 passed`.

### Files changed

- `glassbox/events/models.py`
- `tests/events/test_models.py`
- `tests/test_architecture.py`
- `.superpowers/sdd/task-1-report.md`

### Verification

```text
.venv/bin/python -m pytest tests/events/test_models.py tests/test_architecture.py -v  19 passed
.venv/bin/ruff check .
.venv/bin/mypy glassbox
.venv/bin/lint-imports
git diff --check
```

## Final review-fix report

### Findings resolved

1. Canonical payload validation is now recursive and strict. It accepts only JSON
   primitives, finite floats, mappings with string keys, and lists or tuples of
   accepted values. Accepted mappings and sequences are recursively frozen;
   unsupported values (including `set` and `bytearray`) raise Pydantic validation
   errors before they can be retained or serialized. Existing canonical sorted-key
   JSON output remains unchanged.
2. The events import guard now rejects both absolute `glassbox` imports and only
   those relative imports whose level escapes `glassbox.events`. Its recursive
   tests cover root-level `from ..collector import ...` and `from .. import ...`,
   their nested-module equivalents, and an allowed nested relative import that
   remains inside `glassbox.events`.

### Fresh TDD evidence

RED after adding the payload regressions and before changing the payload validator:

```shell
.venv/bin/python -m pytest tests/events/test_models.py tests/test_architecture.py -v
```

Result: `4 failed, 22 passed`; trace and evidence events accepted both `set` and
`bytearray` payload values instead of raising validation errors.

GREEN after adding strict recursive validation and pre-validation freezing:

```shell
.venv/bin/python -m pytest tests/events/test_models.py tests/test_architecture.py -v
```

Result: `26 passed`.

RED after refining the import-guard tests to distinguish relative imports that
escape the events package from nested imports that remain within it:

```shell
.venv/bin/python -m pytest tests/test_architecture.py -v
```

Result: `1 failed, 8 passed`; the original guard rejected an allowed nested
`from ..collector import ...` import.

GREEN after calculating relative-import escape depth from each source module:

```shell
.venv/bin/python -m pytest tests/test_architecture.py -v
```

Result: `9 passed`.

### Final verification

```text
.venv/bin/python -m pytest tests/events/test_models.py tests/test_architecture.py -v  29 passed
.venv/bin/ruff check .                                                       All checks passed
.venv/bin/mypy glassbox                                                      Success: no issues found in 3 source files
.venv/bin/lint-imports                                                       Contracts: 0 kept, 0 broken
git diff --check                                                              passed
```
