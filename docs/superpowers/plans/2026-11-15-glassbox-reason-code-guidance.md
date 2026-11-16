# Reason-code Guidance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make optional free-text reason codes understandable and prevent browsers from presenting prior entries as Glassbox-defined choices.

**Architecture:** Keep `reason_code` as the existing append-only free-text form value. Change only the two Decision Card inputs and their accessible helper text; HTTP tests verify the rendered contract without touching server parsing, repository storage, or schema.

**Tech Stack:** Jinja2 templates, FastAPI `TestClient`, pytest.

## Global Constraints

- Apply the same guidance to Feedback and Operational override forms.
- Each `reason_code` input uses `autocomplete="off"` as a browser request; do not rename the form field.
- The exact helper copy is: `Reason codes are optional. Glassbox does not define their meanings. Explain any code in the adjacent comment or note.`
- Preserve existing reason-code values verbatim in append-only feedback and override history.
- Do not add a codebook, dropdown, configuration file, schema migration, code validation, or agent-specific semantics.
- P2.4 remains pending a real target-planner session.

---

## File structure

- `glassbox/web/templates/decision_card.html` owns the live Decision Card's override and feedback form markup.
- `tests/web/test_server.py` owns HTTP-level template assertions against a seeded temporary database.

### Task 1: Render accessible optional-code guidance on both planner forms

**Files:**
- Modify: `glassbox/web/templates/decision_card.html:39-56`
- Modify: `tests/web/test_server.py:541-575`

**Interfaces:**
- Consumes: existing `card`, `csrf_token`, and `idempotency_key` template context.
- Produces: unchanged `reason_code` form fields plus two unique help elements referenced by `aria-describedby`.
- Preserves: `POST /decision/{decision_id}/override` and `POST /decision/{decision_id}/feedback` payload keys and repository record shape.

- [ ] **Step 1: Write the failing HTTP regression test**

  Add this test after `test_override_form_exposes_the_complete_current_recommendation_as_json` in `tests/web/test_server.py`:

  ```python
  def test_reason_code_fields_explain_their_optional_free_text_contract(tmp_path: Path) -> None:
      database_path, decision_id, _ = seeded_database(tmp_path)
      client = TestClient(
          app_for(Clock(), tmp_path, database_path), base_url="http://127.0.0.1:8787"
      )
      client.post("/login", data={"access_token": TOKEN})

      response = client.get(f"/decision/{decision_id}")

      assert response.status_code == 200
      assert response.text.count('name="reason_code" autocomplete="off"') == 2
      assert response.text.count(
          "Reason codes are optional. Glassbox does not define their meanings. "
          "Explain any code in the adjacent comment or note."
      ) == 2
      assert 'aria-describedby="override-reason-code-help"' in response.text
      assert 'aria-describedby="feedback-reason-code-help"' in response.text
  ```

- [ ] **Step 2: Run the new test and verify it fails**

  Run:

  ```bash
  .venv/bin/pytest -q tests/web/test_server.py::test_reason_code_fields_explain_their_optional_free_text_contract
  ```

  Expected: failure because the current inputs omit `autocomplete="off"`, helper copy, and the two ARIA references.

- [ ] **Step 3: Add the override-form input and help**

  In `glassbox/web/templates/decision_card.html`, replace the existing override reason-code label with:

  ```html
  <label>Reason code <input name="reason_code" autocomplete="off" aria-describedby="override-reason-code-help"></label>
  <p class="help" id="override-reason-code-help">Reason codes are optional. Glassbox does not define their meanings. Explain any code in the adjacent comment or note.</p>
  ```

  Keep the `Comment` textarea, hidden security fields, action selection, current JSON disclosure, and modified-value textarea unchanged.

- [ ] **Step 4: Add the feedback-form input and help**

  In `glassbox/web/templates/decision_card.html`, replace the existing feedback reason-code label with:

  ```html
  <label>Reason code <input name="reason_code" autocomplete="off" aria-describedby="feedback-reason-code-help"></label>
  <p class="help" id="feedback-reason-code-help">Reason codes are optional. Glassbox does not define their meanings. Explain any code in the adjacent comment or note.</p>
  ```

  Keep the verdict, reasoning-quality selector/help, Note textarea, corrected recommendation textarea, and feedback-history loop unchanged.

- [ ] **Step 5: Run the focused HTTP tests and verify they pass**

  Run:

  ```bash
  .venv/bin/pytest -q tests/web/test_server.py -k 'reason_code or override_form or feedback'
  ```

  Expected: all selected tests pass, confirming both helper contracts render and existing feedback/override POST behavior is unchanged.

- [ ] **Step 6: Run quality checks and commit**

  Run:

  ```bash
  .venv/bin/ruff check glassbox/web tests/web
  .venv/bin/mypy glassbox
  git diff --check
  git add glassbox/web/templates/decision_card.html tests/web/test_server.py
  git commit -m "feat: explain optional reason codes"
  ```

  Expected: Ruff, mypy, and whitespace checks pass; the commit contains only the template and HTTP regression test.

## Plan self-review

- **Spec coverage:** Task 1 covers both forms, browser-autocomplete suppression, explicit optional/free-text guidance, accessibility references, and regression protection. It intentionally leaves persistence and history unchanged.
- **Placeholder scan:** No implementation steps rely on unresolved choices; the exact user-visible string, file paths, assertions, commands, and commit scope are specified.
- **Type consistency:** No Python interface or type changes are introduced. The template field name remains `reason_code`, matching the existing FastAPI route parameters and repository records.
