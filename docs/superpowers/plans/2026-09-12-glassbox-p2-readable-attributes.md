# Readable Attribute Presentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax.

**Goal:** Render scalar recommendation and evidence mappings as readable, agent-neutral attributes in the queue, Decision Card, and static export.

**Architecture:** A single presentation layer in **glassbox.web.read_models** classifies JSON-safe values as a scalar, a scalar-only mapping, or opaque fallback JSON. **ReadService** puts its recommendation view into queue rows; the same views drive every Jinja template.

**Tech Stack:** Python 3.11, dataclasses, Pydantic, Jinja2, FastAPI TestClient, pytest, CSS.

## Global Constraints

- Never infer units or display conventions from an opaque key name: 0.25 renders as 0.25, never 25%.
- A scalar is str, int, finite float, bool, or None. Only mappings containing scalar values become attributes.
- Render bool as Yes/No, None as Not provided, and snake_case keys as title-cased labels.
- Arrays, nested mappings, and non-finite floats remain canonical JSON so unknown agent data is never discarded.
- Do not add dependencies, schema changes, JavaScript, a framework, or agent-specific key logic.
- Preserve the existing web-to-store, never web-to-sdk import boundary.

---

### Task 1: Add shared display models

**Files:**
- Modify: glassbox/web/read_models.py
- Modify: tests/web/test_read_models.py

**Interfaces:**
- Add AttributeView(label: str, value: str).
- Add ValueView(scalar: str | None, attributes: tuple[AttributeView, ...], raw_json: str | None).
- Add RecommendationView(action: str, attributes: tuple[AttributeView, ...], raw_json: str | None).
- Add value_view(value: object) -> ValueView and recommendation_view(value: object) -> RecommendationView.
- Change FieldView to own value: ValueView and DecisionCard to own recommendation: RecommendationView.

- [ ] **Step 1: Write failing model tests**

~~~python
def test_value_view_formats_scalar_mapping_without_inferred_units() -> None:
    view = value_view(
        {"is_estimated": False, "notes": None, "threshold": 0.25, "units": 0}
    )

    assert view.scalar is None
    assert view.raw_json is None
    assert view.attributes == (
        AttributeView("Is Estimated", "No"),
        AttributeView("Notes", "Not provided"),
        AttributeView("Threshold", "0.25"),
        AttributeView("Units", "0"),
    )


def test_value_view_keeps_nested_data_as_canonical_json() -> None:
    view = value_view({"units": 2, "source": {"warehouse": "A"}})

    assert view.attributes == ()
    assert view.raw_json == '{"source":{"warehouse":"A"},"units":2}'
~~~

Extend the existing Decision Card model test to assert evidence {"units": 0} becomes an AttributeView and recommendation {"action": "order", "threshold": 0.25} becomes RecommendationView("order", (AttributeView("Threshold", "0.25"),), None).

- [ ] **Step 2: Verify RED**

Run: .venv/bin/pytest tests/web/test_read_models.py -q

Expected: FAIL because the new models and functions do not exist.

- [ ] **Step 3: Implement the minimal presentation boundary**

~~~python
@dataclass(frozen=True)
class AttributeView:
    label: str
    value: str


@dataclass(frozen=True)
class ValueView:
    scalar: str | None
    attributes: tuple[AttributeView, ...]
    raw_json: str | None


def value_view(value: object) -> ValueView:
    if _is_scalar(value):
        return ValueView(_scalar_text(value), (), None)
    if isinstance(value, dict) and all(_is_scalar(item) for item in value.values()):
        return ValueView(
            None,
            tuple(AttributeView(_label(key), _scalar_text(item)) for key, item in value.items()),
            None,
        )
    return ValueView(None, (), canonical_dumps(value))
~~~

Implement _is_scalar with math.isfinite for floats; _scalar_text returns Yes, No, Not provided, or canonical JSON numeric text; _label returns key.replace("_", " ").title(). recommendation_view reads a string action only from a mapping, removes Action from secondary attributes, and exposes raw_json only for a non-scalar mapping. Build all evidence FieldViews via value_view and the DecisionCard recommendation via recommendation_view.

- [ ] **Step 4: Verify GREEN**

Run: .venv/bin/pytest tests/web/test_read_models.py -q

Expected: PASS, including evidence, alternatives, overrides, and trace models.

- [ ] **Step 5: Commit**

~~~bash
git add glassbox/web/read_models.py tests/web/test_read_models.py
git commit -m "feat: add agent-neutral attribute display models"
~~~

### Task 2: Render shared views in queue, card, and export

**Files:**
- Modify: glassbox/web/read_models.py
- Modify: glassbox/web/read_service.py
- Modify: glassbox/web/templates/queue.html
- Modify: glassbox/web/templates/decision_card.html
- Modify: glassbox/web/templates/decision_export.html
- Modify: glassbox/web/static/glassbox.css
- Modify: tests/web/test_read_service.py
- Modify: tests/web/test_server.py
- Modify: tests/web/test_export.py
- Modify: tests/test_cli.py

**Interfaces:**
- Replace QueueRow.recommendation_summary with QueueRow.recommendation: RecommendationView.
- Every template selects exactly one ValueView branch: scalar, attributes, or raw JSON.
- Export uses the same DecisionCard views and contains no export-only presentation code.

- [ ] **Step 1: Write failing cross-surface tests**

~~~python
def test_queue_row_uses_the_same_recommendation_view_as_the_card(tmp_path: Path) -> None:
    service = ReadService(_strict_database_with_decision(tmp_path))

    row = service.queue(QueueRequest()).rows[0]
    card = service.decision_card(DECISION_ID)

    assert card is not None
    assert row.recommendation == card.recommendation
    assert row.recommendation.action == "order"


def test_queue_renders_scalar_recommendation_attributes_not_compact_json(tmp_path: Path) -> None:
    database_path, _, _ = seeded_database(tmp_path)
    client = TestClient(app_for(Clock(), tmp_path, database_path), base_url="http://127.0.0.1")
    client.post("/login", data={"access_token": TOKEN})

    response = client.get("/")

    assert "Order" in response.text
    assert '{"action":"order"}' not in response.text
~~~

Use seeded recommendation {"action": "order", "threshold": 0.25} and evidence {"is_estimated": False, "units": 2}. Assert live card and export HTML include Threshold, 0.25, Is Estimated, No, and Units, but not their compact scalar JSON. In the existing export fixture, add nested recommendation {"action": "order", "constraints": {"minimum": 2}} and evidence {"location": {"warehouse": "A"}}; assert canonical fallback JSON is present: {"action":"order","constraints":{"minimum":2}} and {"location":{"warehouse":"A"}}.

- [ ] **Step 2: Verify RED**

Run: .venv/bin/pytest tests/web/test_read_service.py tests/web/test_server.py tests/web/test_export.py tests/test_cli.py -q

Expected: FAIL because QueueRow still owns recommendation_summary and templates render compact JSON.

- [ ] **Step 3: Implement the one rendering path**

In ReadService.queue, call recommendation_view on the event recommendation and pass that view to QueueRow. Remove recommendation_summary only after no caller remains.

Use this branch for every evidence value in card and export templates:

~~~jinja2
{% if field.value.scalar is not none %}
{{ field.value.scalar }}
{% elif field.value.attributes %}
<dl class="attribute-list">{% for attribute in field.value.attributes %}
  <div><dt>{{ attribute.label }}</dt><dd>{{ attribute.value }}</dd></div>
{% endfor %}</dl>
{% else %}
<pre>{{ field.value.raw_json }}</pre>
{% endif %}
~~~

Render queue Recommendation as action first, then secondary attributes with the same definition list. For raw recommendation fallback, render a details disclosure containing preformatted JSON. The full-recommendation card/export section uses attributes when present and raw JSON only otherwise. Add compact attribute-list CSS suitable for table cells and cards.

- [ ] **Step 4: Verify GREEN**

Run: .venv/bin/pytest tests/web/test_read_service.py tests/web/test_server.py tests/web/test_export.py tests/test_cli.py -q

Expected: PASS, including pagination, feedback, overrides, standalone export, and JSON fallback tests.

- [ ] **Step 5: Commit**

~~~bash
git add glassbox/web/read_models.py glassbox/web/read_service.py glassbox/web/templates/queue.html glassbox/web/templates/decision_card.html glassbox/web/templates/decision_export.html glassbox/web/static/glassbox.css tests/web/test_read_service.py tests/web/test_server.py tests/web/test_export.py tests/test_cli.py
git commit -m "feat: render readable decision attributes"
~~~

### Task 3: Run the complete quality gate

**Files:**
- No production files; validates Tasks 1 and 2.

**Interfaces:**
- Consumes ValueView and RecommendationView plus all queue/card/export renderers.
- Produces evidence that the change is ready for browser review.

- [ ] **Step 1: Run the complete quality gate**

~~~bash
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/mypy glassbox
.venv/bin/lint-imports
git diff --check
~~~

Expected: all tests pass; Ruff and mypy report no issues; all six import-linter contracts are kept.

## Plan Self-Review

- **Spec coverage:** Task 1 implements scalar classification, neutral labels and values, and canonical fallback. Task 2 drives queue, live card, and static export through the same display model and tests all three. Task 3 supplies the full verification gate.
- **Placeholder scan:** every task has exact files, interfaces, failing tests, commands, and expected results.
- **Type consistency:** ValueView is the evidence interface throughout; RecommendationView is the recommendation interface in DecisionCard and QueueRow; raw JSON is named raw_json everywhere.

