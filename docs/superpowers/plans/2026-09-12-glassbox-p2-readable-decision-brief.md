# Glassbox P2 Readable Decision Brief Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the generic Glassbox queue and Decision Card understandable to a planner without changing persisted telemetry.

**Architecture:** `read_models` converts event data into JSON-safe, immutable presentation values and derives only contract-level labels. Jinja templates render those display values in a compact Decision Brief layout, with one local stylesheet and no external assets.

**Tech Stack:** Python 3.11, Pydantic event models, Jinja2 templates, FastAPI static files, pytest.

## Global Constraints

- Keep the web package agent-neutral; do not introduce replenishment-specific code or schema fields.
- Templates must not render event mappings, tuples, or `FrozenDict` instances directly.
- Preserve canonical JSON fidelity for opaque unknown recommendation and alternative values.
- Use local CSS only; add no frontend dependency or external asset.
- Keep the existing authenticated read/write routes, CSRF controls, feedback ledger, and override persistence unchanged.
- Use test-driven development: observe each new behavior fail before implementation.

---

### Task 1: JSON-safe Decision Brief presentation models

**Files:**
- Modify: `glassbox/web/read_models.py:32-72,137-186`
- Modify: `tests/web/test_read_models.py:1-110`

**Interfaces:**
- Consumes: `StoredDecision.event`, `EvidenceEvent`, `canonical_dumps`.
- Produces: `FieldView`, `AlternativeView`, and additional `DecisionCard` display fields used by templates.

- [ ] **Step 1: Write failing model tests**

Add a decision containing nested evidence and an alternative with a reason. Assert that the resulting card exposes `entity_label == "sku · sku-1"`, `recommended_action == "order"`, field JSON without `FrozenDict`, and a readable alternative label.

```python
def test_card_exposes_json_safe_decision_brief_values() -> None:
    stored = _stored_decision()
    evidence = stored.evidence[0].model_copy(update={"field_value": {"units": 0}})
    decision = stored.event.model_copy(
        update={"alternatives_considered": [{"action": "wait", "reason": "incoming stock"}]}
    )

    card = build_decision_card(StoredDecision(decision, (evidence,)), ())

    assert card.entity_label == "sku · sku-1"
    assert card.recommended_action == "order"
    assert card.evidence_groups[0].fields[0].display_value == '{"units":0}'
    assert card.alternatives[0].summary == "Wait — incoming stock"
    assert "FrozenDict" not in card.evidence_groups[0].fields[0].display_value
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/web/test_read_models.py::test_card_exposes_json_safe_decision_brief_values -v`

Expected: FAIL because `DecisionCard` lacks the readable display fields and evidence fields still contain events.

- [ ] **Step 3: Add minimal immutable display models and builders**

Replace event-bearing evidence fields with a `FieldView(field_name: str, display_value: str)`. Add `AlternativeView(summary: str, detail: str | None)`, plus `entity_label`, `recommended_action`, `recommendation_detail`, and `alternatives` to `DecisionCard`. Use `event.model_dump(mode="json")` before inspecting opaque payloads; `canonical_dumps` supplies every JSON fallback.

```python
@dataclass(frozen=True)
class FieldView:
    field_name: str
    display_value: str

@dataclass(frozen=True)
class AlternativeView:
    summary: str
    detail: str | None

def _json_display(value: object) -> str:
    return canonical_dumps(value)

def _alternative_view(value: object) -> AlternativeView:
    if isinstance(value, dict) and isinstance(value.get("action"), str):
        action = value["action"].replace("_", " ").capitalize()
        reason = value.get("reason")
        if isinstance(reason, str) and reason:
            return AlternativeView(f"{action} — {reason}", None)
    return AlternativeView(_json_display(value), _json_display(value))
```

Build every evidence `FieldView` from `field.model_dump(mode="json")["field_value"]`; derive `recommended_action` from a string `recommendation["action"]`, otherwise `"Not specified"`. Preserve `recommendation_detail` as canonical JSON.

- [ ] **Step 4: Run model tests to verify they pass**

Run: `pytest tests/web/test_read_models.py -v`

Expected: PASS, including existing citation and override-status tests.

- [ ] **Step 5: Commit the display-model boundary**

```bash
git add glassbox/web/read_models.py tests/web/test_read_models.py
git commit -m "feat: add readable decision brief models"
```

### Task 2: Render the readable queue and Decision Card

**Files:**
- Modify: `glassbox/web/templates/base.html`
- Modify: `glassbox/web/templates/queue.html`
- Modify: `glassbox/web/templates/decision_card.html`
- Modify: `glassbox/web/static/glassbox.css`
- Modify: `tests/web/test_server.py:218-228`

**Interfaces:**
- Consumes: the `DecisionCard` and `QueueRow` display values from Task 1.
- Produces: readable live HTML without object representations, while retaining current routes and form field names.

- [ ] **Step 1: Write failing HTTP rendering tests**

Seed a card with a nested evidence mapping and a readable alternative, authenticate, then assert entity/action/rationale and formatted evidence appear. Assert no output contains `FrozenDict` or `object at 0x`.

```python
def test_decision_card_renders_a_readable_brief_without_python_values(tmp_path: Path) -> None:
    database_path, decision_id, _ = seeded_database(tmp_path)
    client = TestClient(app_for(Clock(), tmp_path, database_path), base_url="http://127.0.0.1")
    client.post("/login", data={"access_token": TOKEN})

    response = client.get(f"/decision/{decision_id}")

    assert response.status_code == 200
    assert "sku · sku-1" in response.text
    assert "Recommended action" in response.text
    assert "Inventory is low." in response.text
    assert "FrozenDict" not in response.text
    assert "object at 0x" not in response.text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/web/test_server.py::test_decision_card_renders_a_readable_brief_without_python_values -v`

Expected: FAIL because the current template has no Decision Brief or JSON-safe evidence display.

- [ ] **Step 3: Replace the templates with semantic, accessible sections**

Use a `decision-brief` definition list for type/entity/action/confidence, `pre` blocks for full JSON, one table per evidence group, and an `ul` for alternatives. Keep hidden CSRF/idempotency inputs and every existing form field name unchanged. The queue link text becomes `{{ row.decision_type|replace('_', ' ')|title }} · {{ row.entity_label }}` with the ULID in a metadata line.

```html
<section class="decision-brief" aria-labelledby="decision-summary">
  <p class="eyebrow">Decision brief</p>
  <h1 id="decision-summary">{{ card.decision_type|replace('_', ' ')|title }}</h1>
  <dl class="fact-grid">
    <div><dt>Entity</dt><dd>{{ card.entity_label }}</dd></div>
    <div><dt>Recommended action</dt><dd>{{ card.recommended_action }}</dd></div>
    <div><dt>Confidence</dt><dd>{{ card.confidence_band.value|title }} ({{ "%.0f"|format(card.decision.event.confidence * 100) }}%)</dd></div>
  </dl>
  <p class="rationale">{{ card.decision.event.rationale }}</p>
</section>
```

Add responsive local CSS for `main`, `.decision-brief`, `.fact-grid`, `.section-card`, `.form-stack`, `.metadata`, `pre`, and tables. Do not modify FastAPI routes or add a stylesheet dependency.

- [ ] **Step 4: Run route and full tests to verify they pass**

Run: `pytest tests/web/test_server.py -v && pytest -q && ruff check . && mypy glassbox && lint-imports`

Expected: all tests pass; ruff, mypy, and all import-linter contracts are clean.

- [ ] **Step 5: Commit the readable live interface**

```bash
git add glassbox/web/templates glassbox/web/static/glassbox.css tests/web/test_server.py
git commit -m "feat: render readable planner decision brief"
```

### Task 3: Browser verification and usability-session update

**Files:**
- Modify: `docs/p2-planner-usability.md:42-45`

**Interfaces:**
- Consumes: the running scratch database and the rendered routes from Task 2.
- Produces: a documented precondition that the repaired interface is visually reviewed before a real participant session.

- [ ] **Step 1: Write the documentation assertion as a checklist entry**

Add this statement below the pending-result note:

```markdown
Pre-session interface review: confirm the queue identifies each decision by type and entity, and every Decision Card visibly exposes its action, rationale, readable evidence, and alternatives before recruiting the target planner.
```

- [ ] **Step 2: Perform the browser check**

Start the local server with the non-production scratch database, log in, inspect the queue and one card at desktop width, and confirm no Python object representation is visible.

- [ ] **Step 3: Run final verification**

Run: `pytest -q && ruff check . && mypy glassbox && lint-imports && git status --short`

Expected: all checks pass and only the usability-documentation edit is pending.

- [ ] **Step 4: Commit and push**

```bash
git add docs/p2-planner-usability.md
git commit -m "docs: add readable-card usability precheck"
git push origin main
```
