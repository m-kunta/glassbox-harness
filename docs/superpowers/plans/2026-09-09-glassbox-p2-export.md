# Glassbox P2.4 Single-Decision Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a safe `glassbox export --decision <id>` command that writes one standalone, read-only Decision Card HTML file.

**Architecture:** The CLI reuses its top-level `--database` setting and reads through `ReadService.decision_card()`. A small export module owns loopback live-link validation, dedicated-template rendering with inline CSS, and guarded single-file output. It never starts FastAPI, opens a write-capable database, or exposes feedback mutation controls.

**Tech Stack:** Python 3.11+, argparse, Jinja2, SQLite read-only mode, pytest, Ruff, mypy.

## Global Constraints

- Export only one decision; bulk `--queue --since` remains deferred.
- Open the source database only through `Database.open_read_only` via `ReadService`.
- Output is standalone HTML with inline CSS, no JavaScript, external assets, form, CSRF token, cookie, or POST URL.
- Optional `--live-base-url` accepts only loopback HTTP origins; no live link is rendered when omitted.
- An existing output requires explicit `--overwrite`; errors disclose neither SQLite internals nor credentials.
- `web` may import `store`, never `sdk`; no new runtime dependency is introduced.

## File structure

| File | Responsibility |
| --- | --- |
| `glassbox/web/export.py` | Render a `DecisionCard` as a standalone document and safely write it once. |
| `glassbox/web/templates/decision_export.html` | Static-only card markup with no live controls or assets. |
| `glassbox/cli.py` | Parse export arguments and translate typed exporter failures into concise CLI status codes. |
| `tests/web/test_export.py` | Renderer, loopback-link, escaping, and output-safety tests. |
| `tests/test_cli.py` | CLI export success, missing decision, and overwrite behavior. |
| `README.md`, `TODO.md` | Document command and mark export implementation complete only after verification. |

## Task 1: Static export renderer

**Files:**
- Create: `glassbox/web/export.py`
- Create: `glassbox/web/templates/decision_export.html`
- Test: `tests/web/test_export.py`

**Interfaces:**
- Consumes: `glassbox.web.read_models.DecisionCard` and `glassbox.web.read_service.ReadService`.
- Produces: `render_decision_export(card: DecisionCard, live_base_url: str | None) -> str`, `write_decision_export(path: Path, html: str, overwrite: bool) -> None`, and `ExportError`.

- [ ] **Step 1: Write failing renderer tests.**

```python
def test_rendered_export_is_standalone_and_read_only(card: DecisionCard) -> None:
    html = render_decision_export(card, None)
    assert "<style>" in html
    assert "<form" not in html
    assert "csrf_token" not in html
    assert "<script" not in html
    assert 'href="/static/' not in html

def test_export_escapes_persisted_feedback(card_with_hostile_feedback: DecisionCard) -> None:
    html = render_decision_export(card_with_hostile_feedback, None)
    assert "&lt;script&gt;" in html
    assert "<script>" not in html
```

- [ ] **Step 2: Run the renderer tests and confirm import failure.**

Run: `.venv/bin/python -m pytest --import-mode=importlib tests/web/test_export.py -q`

Expected: FAIL because `glassbox.web.export` does not exist.

- [ ] **Step 3: Implement the renderer and guarded file writer.**

```python
class ExportError(RuntimeError):
    pass

def render_decision_export(card: DecisionCard, live_base_url: str | None) -> str:
    return _templates.get_template("decision_export.html").render(
        card=card, live_url=_live_url(card, live_base_url), css=_CSS.read_text(encoding="utf-8")
    )

def write_decision_export(path: Path, html: str, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise ExportError("output already exists; pass --overwrite to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
```

Validate `live_base_url` with `urllib.parse.urlsplit`: scheme exactly `http`, no credentials/query/fragment/path beyond empty or `/`, hostname exactly `127.0.0.1` or `::1`, and explicit valid port. The template takes only `card`, `live_url`, and inline `css`; it has no form or static-asset link.

- [ ] **Step 4: Add failing output safety tests, then implement the minimal checks.**

```python
def test_writer_refuses_existing_output_without_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "card.html"
    target.write_text("old", encoding="utf-8")
    with pytest.raises(ExportError, match="--overwrite"):
        write_decision_export(target, "new", overwrite=False)
    write_decision_export(target, "new", overwrite=True)
    assert target.read_text(encoding="utf-8") == "new"
```

- [ ] **Step 5: Run focused export tests and quality checks.**

Run: `.venv/bin/python -m pytest --import-mode=importlib tests/web/test_export.py -q && .venv/bin/ruff check glassbox tests/web && .venv/bin/mypy glassbox`

Expected: PASS.

- [ ] **Step 6: Commit renderer work.**

```shell
git add glassbox/web/export.py glassbox/web/templates/decision_export.html tests/web/test_export.py
git commit -m "feat: add static decision export renderer"
```

## Task 2: CLI command and read-only integration

**Files:**
- Modify: `glassbox/cli.py`
- Modify: `tests/test_cli.py`
- Test: `tests/web/test_export.py`

**Interfaces:**
- Consumes: `ReadService(path).decision_card(decision_id)`, `render_decision_export`, `write_decision_export`, and `ExportError` from Task 1.
- Produces: `glassbox export --decision ID --output FILE [--live-base-url URL] [--overwrite]`.

- [ ] **Step 1: Write failing CLI tests.**

```python
def test_export_writes_one_read_only_card(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    database, decision_id = seeded_database(tmp_path)
    target = tmp_path / "card.html"
    assert main(["--database", str(database), "export", "--decision", decision_id, "--output", str(target)]) == 0
    assert "Feedback is read-only" in target.read_text(encoding="utf-8")

def test_export_missing_decision_and_existing_output_are_nonzero(...) -> None:
    assert main([... "--decision", MISSING_ID, "--output", str(target)]) == 1
    assert main([... "--decision", decision_id, "--output", str(existing)]) == 2
```

- [ ] **Step 2: Run CLI tests and confirm the command is absent.**

Run: `.venv/bin/python -m pytest --import-mode=importlib tests/test_cli.py -q`

Expected: FAIL because argparse rejects `export`.

- [ ] **Step 3: Add parser and command dispatch.**

```python
export_command = commands.add_parser("export", help="write one static Decision Card HTML file")
export_command.add_argument("--decision", required=True)
export_command.add_argument("--output", required=True)
export_command.add_argument("--live-base-url")
export_command.add_argument("--overwrite", action="store_true")

if arguments.command == "export":
    try:
        card = ReadService(Path(arguments.database)).decision_card(arguments.decision)
        if card is None:
            print("glassbox: decision not found", file=sys.stderr)
            return 1
        html = render_decision_export(card, arguments.live_base_url)
        write_decision_export(Path(arguments.output), html, overwrite=arguments.overwrite)
    except (ExportError, ReadOnlyDatabaseError):
        print("glassbox: unable to export decision", file=sys.stderr)
        return 2
    return 0
```

Do not echo the database path, output path, raw SQLite exception, or live URL in errors. Read-only database errors and malformed live URL/input share the generic nonzero command failure.

- [ ] **Step 4: Add a source-database no-mutation test.**

```python
def test_cli_export_never_mutates_the_source_database(...) -> None:
    before = database.read_bytes()
    assert main([...]) == 0
    assert database.read_bytes() == before
```

Keep the writer closed before snapshotting the database; the export path must not create, migrate, or modify it.

- [ ] **Step 5: Run focused and full automated verification.**

Run: `.venv/bin/python -m pytest --import-mode=importlib tests/test_cli.py tests/web/test_export.py -q && .venv/bin/python -m pytest --import-mode=importlib -q && .venv/bin/ruff check . && .venv/bin/mypy glassbox && .venv/bin/lint-imports`

Expected: all tests and six import contracts pass.

- [ ] **Step 6: Commit CLI integration.**

```shell
git add glassbox/cli.py tests/test_cli.py tests/web/test_export.py
git commit -m "feat: add decision export command"
```

## Task 3: Document export and record usability evidence

**Files:**
- Modify: `README.md`
- Modify: `TODO.md`
- Create: `docs/p2-planner-usability.md`

- [ ] **Step 1: Document the exact export command and read-only behavior.**

Add a README example using `--database`, `--decision`, `--output`, and optional loopback `--live-base-url`. State that output is self-contained/read-only and that feedback/override actions require the live application.

- [ ] **Step 2: Create the structured usability checklist artifact.**

Record five representative-card rows with verdict, two evidence items, rejected alternative, evidence timestamp, completion result, and observations. Include the 90% threshold and explicitly state that operational override scenarios are deferred to P2.5.

- [ ] **Step 3: Mark only verified P2.4 work complete.**

Check the static-export item in `TODO.md` after Task 2 passes. Check the usability item only after the completed checklist records at least 90% task completion; do not fabricate participant results.

- [ ] **Step 4: Run the full quality gate and commit.**

Run: `.venv/bin/python -m pytest --import-mode=importlib -q && .venv/bin/ruff check . && .venv/bin/mypy glassbox && .venv/bin/lint-imports`

Commit command after verified documentation/usability evidence:

```shell
git add README.md TODO.md docs/p2-planner-usability.md
git commit -m "docs: complete decision export workflow"
```

## Self-review

- Task 1 creates standalone static rendering, exact loopback URL validation, escaping, and guarded writes.
- Task 2 connects the renderer to the established top-level `--database` command path and proves no database mutation.
- Task 3 documents the command, preserves the P2.5 usability boundary, and refuses to claim human validation without recorded evidence.
- No task adds bulk queue export, trace/blob export, JavaScript, or a write path.
