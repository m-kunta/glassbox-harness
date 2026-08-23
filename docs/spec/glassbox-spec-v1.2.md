# Glassbox — Agent Evaluation, Observability & Explainability Harness

**Version:** 1.2 (implementation-ready build spec — supersedes v1.1)
**Owner:** Mohith Kunta
**Target repo:** `glassbox`
**Intended consumers:** Claude Code / Codex as implementing agents
**First release scope:** P0–P2 vertical slice

---

## 0. Decisions locked in this version

| Decision | Choice | Rationale |
|---|---|---|
| First release | P0–P2 vertical slice | P0 alone surfaces nothing a human reads; the label flywheel starts at P2 |
| Architecture | Modular Python monolith | Package boundaries are the real seams; extraction later is mechanical |
| Card surface | Single localhost server (read + write) | Override capture is the label pipeline — no friction in front of it |
| Static export | Secondary, read-only | For sharing/archiving, not a degraded write path |
| Frontend | Jinja2 + HTMX, server-rendered | Same templates serve live and static; no build step, no JS toolchain review |
| Counterfactuals | Deferred to P3 | Requires side-effect-free agent entry point — a dependency on someone else's code |
| P0 checkpoint | Retained as a stop-and-report gate | Inspect SDK ergonomics before layering eval on a bad abstraction |
| Evidence ownership | Explicit `decision_context` | Prevents evidence from leaking between decisions in batch traces |
| Rationale citations | Structured evidence IDs | Makes evidence-grounding assertions deterministic |
| Feedback history | Append-only, idempotent events | Preserves audit history and prevents duplicate submissions |
| Parquet and runtime OTLP export | Deferred beyond first release | Avoids building retention/export machinery before demand exists |

---

## 1. Problem statement

You have several supply-chain reasoning agents in flight: replenishment exception triage, supplier collaboration briefing, safety stock rationalization, promo–forecast reconciliation. Each produces *judgments*, not deterministic outputs. Four unanswered questions block production adoption:

1. **Did it work?** No golden set, no regression gate. A prompt change can silently degrade quality.
2. **Why did it say that?** A planner who can't see the reasoning chain won't act on the recommendation.
3. **Is it still working?** Data drifts, vendors change behavior, seasonality shifts. Quality decays invisibly.
4. **Was it actually right?** Ground truth arrives 2–8 weeks later — the flagged exception either became a stockout or it didn't.

Glassbox is the layer underneath the agents that answers all four. It is **agent-agnostic**: it wraps existing agents rather than requiring them to be rewritten.

### The differentiated piece

Most LLM observability tooling handles (1)–(3) with immediate rubric scoring. Supply chain uniquely needs **deferred outcome reconciliation** — linking a decision made in week 1 to a business outcome observed in week 6, using the two strongest real-world signals available:

- **Planner override rate** — did the human accept, modify, or reject?
- **Realized outcome** — did the predicted condition actually occur?

Build this into the data model at P0. Retrofitting the decision→outcome join later is a rewrite.

---

## 2. Design principles

| Principle | Implication |
|---|---|
| **Non-invasive instrumentation** | Wrapping an agent must require ≤5 lines of change. Decorator + context manager. Never fork agent logic. |
| **Evidence is first-class** | Every decision carries pointers to the source records that produced it. No evidence → unexplainable → fails eval. |
| **Deferred truth** | Decisions and outcomes are separate tables joined asynchronously. Never assume ground truth at decision time. |
| **Local-first** | SQLite for the first release. No external SaaS dependency. Runs entirely on a laptop, no egress required. Parquet retention is deferred until volume requires it. |
| **OpenTelemetry-shaped** | OTel GenAI semantic conventions for span attributes, so export to enterprise APM later needs no rework. |
| **Fail open** | If Glassbox errors, the wrapped agent still runs. Observability never takes down the workload. |
| **No client-side application framework through P5** | Server-rendered HTML with minimal progressive enhancement. Reconsider only if measured interaction needs cannot be met this way. |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Instrumented Agents                                         │
│  replenishment-triage-ai │ supplier-collab-ai │ promo-recon  │
└───────────────┬─────────────────────────────────────────────┘
                │  glassbox.sdk  (@trace, @decision, evidence)
                ▼
┌─────────────────────────────────────────────────────────────┐
│  Collector — bounded async writer, fail-open, canonical events│
└───────────────┬─────────────────────────────────────────────┘
                ▼
┌─────────────────────────────────────────────────────────────┐
│  Store    SQLite (indexed, WAL)                               │
│           blobs/ (content-addressed prompts & completions)   │
└───────┬──────────────────────────┬──────────────────────────┘
        │                          │
        ▼                          ▼
┌──────────────────┐   ┌───────────────────────────────────────┐
│  Eval Engine     │   │  Web App  (Jinja2 + HTMX)             │
│  - golden sets   │   │  /            Decision Queue          │
│  - assertions    │   │  /decision/<id>  Decision Card  [W]   │
│  - LLM judge  P3 │   │  /trace/<id>     Trace View           │
│  - drift      P3 │   │        │                              │
└────────┬─────────┘   │        └── same templates ──┐         │
         │             └───────────────────────────  │         │
         │                                           ▼         │
         │                              static export (read-only)
         ▼
┌─────────────────────────────────────────────────────────────┐
│  Surfaces:  CLI / local exit-code gate (P1) │ Dashboard + GitHub Actions wiring (P5) │
└─────────────────────────────────────────────────────────────┘
         ▲
┌────────┴────────────────────────────────────────────────────┐
│  Outcome Ingest (P4) — overrides, realized stockouts, PO Δ   │
└─────────────────────────────────────────────────────────────┘
```

### Module boundary rules — enforce in CI

- `events/` contains dependency-neutral canonical event contracts and imports no Glassbox package.
- `sdk/` may import `events/` but imports nothing from `collector/`, `store/`, `eval/`, `explain/`, or `web/`.
- `collector/` may import `events/` and `store/`.
- `store/` imports nothing from `eval/`, `explain/`, or `web/`.
- `eval/` and `explain/` may import `store/`; never the reverse.
- `web/` may import `store/` and `explain/`; never `sdk/`.

Add an import-linter config to `pyproject.toml` and fail the build on violation. These boundaries are what make the monolith extractable later.

---

## 4. Repository layout

```
glassbox/
├── pyproject.toml               # includes import-linter contracts
├── README.md
├── glassbox/
│   ├── sdk/
│   │   ├── tracer.py            # @trace, span(), decision()
│   │   ├── evidence.py          # evidence(), redaction hooks
│   │   ├── context.py           # contextvars trace propagation
│   │   └── config.py
│   ├── events/
│   │   └── models.py            # dependency-neutral canonical events
│   ├── collector/
│   │   ├── buffer.py            # async queue, backpressure, fail-open
│   │   └── writer.py            # bounded queue → SQLite writer
│   ├── store/
│   │   ├── schema.sql
│   │   ├── models.py            # pydantic
│   │   ├── repository.py        # query layer
│   │   ├── blobs.py             # content-addressed prompt/completion store
│   │   └── migrations/
│   ├── eval/
│   │   ├── runner.py
│   │   ├── assertions.py        # deterministic checks
│   │   ├── metrics.py
│   │   ├── target.py            # EvaluationTarget protocol + loader
│   │   └── suites/
│   │       └── replenishment_triage.yaml
│   ├── explain/
│   │   ├── decision_card.py     # builds the card view-model
│   │   └── export.py            # static self-contained HTML
│   ├── web/
│   │   ├── app.py               # FastAPI + Jinja2
│   │   ├── routes/
│   │   │   ├── queue.py
│   │   │   ├── decision.py      # GET card, POST feedback
│   │   │   └── trace.py
│   │   ├── templates/
│   │   │   ├── base.html
│   │   │   ├── queue.html
│   │   │   ├── decision_card.html   # shared: live + static
│   │   │   ├── _evidence_table.html
│   │   │   ├── _feedback.html       # HTMX POST target
│   │   │   └── trace.html
│   │   └── static/              # one CSS file, htmx.min.js vendored
│   └── cli.py                   # glassbox <serve|eval|export|trace>
├── tests/
├── integrations/                 # created in P1; per-agent EvaluationTarget adapters
│   ├── __init__.py
│   └── replenishment_triage.py   # exposes run_case
├── goldens/replenishment_triage/
│   ├── cases/
│   └── manifest.yaml
└── examples/wrap_existing_agent.py
```

Only create modules required by the active phase. The complete forward-compatible database schema is required at P0, but empty P3–P5 Python packages are not. Runtime OTLP export, Parquet rollover, drift, outcome adapters, notifications, the LLM judge, and counterfactual code are introduced in their owning phases.

---

## 5. Data model

ULIDs for all IDs (sortable, no coordination). **Create every table at P0**, including `outcomes` and `overrides`, even though `outcomes` stays empty until P4.

### `traces`
| column | type | notes |
|---|---|---|
| trace_id | TEXT PK | ULID |
| agent_name | TEXT | e.g. `replenishment-triage-ai` |
| agent_version | TEXT | git SHA + prompt hash |
| started_at / ended_at | TIMESTAMP | |
| status | TEXT | ok / error / partial |
| environment | TEXT | dev / shadow / prod |
| input_ref | TEXT | blob pointer |
| total_tokens, total_cost_usd, latency_ms | numeric | |

### `spans`
| column | type | notes |
|---|---|---|
| span_id | TEXT PK | |
| trace_id, parent_span_id | TEXT | |
| name, span_kind | TEXT | `llm` / `retrieval` / `tool` / `compute` |
| attributes | JSON | OTel GenAI conventions |
| prompt_ref, completion_ref | TEXT | blob pointers, never inline |
| model, temperature, tokens_in, tokens_out, latency_ms | | |

### `decisions`
The unit that matters. One row per recommendation emitted.

| column | type | notes |
|---|---|---|
| decision_id | TEXT PK | |
| trace_id | TEXT FK | |
| agent_name / agent_version | TEXT | denormalized trace identity for queue filtering and archival |
| entity_type / entity_id | TEXT | e.g. `sku_dc`, `0123456-DC04` |
| decision_type | TEXT | `flag_exception`, `recommend_param_change` |
| recommendation | JSON | structured, not prose |
| rationale | TEXT | model-generated narrative |
| rationale_citations | JSON | ordered evidence IDs referenced by the rationale |
| confidence | REAL | 0–1 |
| alternatives_considered | JSON | required — see §7 |
| decided_at | TIMESTAMP | |

Index on `(agent_name, decided_at)`, `(entity_type, entity_id)`, `(decision_type, confidence)` — the queue screen filters on all three. Denormalized agent identity is copied from the trace when the decision is created.

### `evidence`
| column | type | notes |
|---|---|---|
| evidence_id, decision_id | TEXT | evidence ID is unique within its decision |
| source_system | TEXT | e.g. `BY_Fulfillment`, `EDM` |
| source_ref | TEXT | table/key/query that produced it |
| field_name | TEXT | stable machine-readable field name |
| field_value_json | TEXT | canonical JSON; preserves number, Boolean, null, and structured values |
| weight | REAL | agent-declared influence |
| retrieved_at | TIMESTAMP | staleness detection |

### `overrides` — **populated from P2**
| column | type | notes |
|---|---|---|
| override_id, decision_id | TEXT | |
| actor | TEXT | planner ID (hashed) |
| action | TEXT | `accepted` / `modified` / `rejected` |
| modified_value | JSON | |
| reason_code, free_text | TEXT | |
| created_at | TIMESTAMP | |
| supersedes_override_id | TEXT nullable | previous response replaced by this event |
| idempotency_key | TEXT UNIQUE | prevents duplicate form submissions |

### `outcomes` — schema at P0, populated at P4
| column | type | notes |
|---|---|---|
| outcome_id, decision_id | TEXT | |
| outcome_type | TEXT | `stockout_occurred`, `no_action_needed` |
| observed_at | TIMESTAMP | |
| horizon_days | INT | lag between decision and observation |
| value | JSON | |
| label | TEXT | `tp` / `fp` / `tn` / `fn` — derived |

### `eval_runs` / `eval_results`
Suite ID, suite version, agent version, case ID, assertion name, pass/fail, score, judge rationale, run timestamp.

### Storage invariants

- Enable `PRAGMA foreign_keys = ON`, WAL journal mode, and a configurable busy timeout on every connection.
- Use UTC timestamps in RFC 3339 format everywhere.
- Enforce `confidence BETWEEN 0 AND 1` and constrained values for trace status and override action.
- Define foreign keys for spans, decisions, evidence, overrides, and outcomes. A trace with no overrides or outcomes may be archival-deleted with its spans and decisions. If any decision has an override or outcome, deletion is rejected (`ON DELETE RESTRICT`) so planner feedback and realized truth cannot be silently erased. Individual decision deletion is not exposed in the first release.
- Overrides are append-only audit events. A corrected response points to the prior event with `supersedes_override_id`; queries derive the latest effective response.
- `actor` comes from `GLASSBOX_ACTOR` or local configuration and is hashed with a salt generated once at initialization and persisted in the local Glassbox configuration. The salt is reused across processes and upgrades for that installation; an unsalted employee identifier must never be persisted.

---

## 6. SDK contract

The integration surface stays this small:

```python
import glassbox as gb

gb.init(agent="replenishment-triage-ai", version=GIT_SHA, env="shadow")

@gb.trace
def triage_batch(exceptions: list[dict]) -> list[dict]:
    for exc in exceptions:
        with gb.decision_context(
            entity_type="sku_dc",
            entity_id=f"{exc['sku']}-{exc['dc']}",
            decision_type="flag_exception",
        ) as decision:
            with gb.span("retrieve_context", kind="retrieval"):
                ctx = fetch_context(exc)
                gb.evidence(
                    evidence_id="inventory_position",
                    source_system="BY_Fulfillment",
                    source_ref=f"item_loc/{exc['sku']}/{exc['dc']}",
                    fields={"on_hand": ctx.on_hand, "lead_time_var": ctx.ltv},
                )

            with gb.span("reason", kind="llm"):
                result = llm_call(...)

            decision.complete(
                recommendation={"action": result.action, "urgency": result.urgency},
                rationale=result.rationale,
                rationale_citations=["inventory_position"],
                confidence=result.confidence,
                alternatives_considered=result.alternatives,
            )
```

**Hard requirements**
- `gb.init()` failing logs a warning and no-ops. Never raises.
- Prompts and completions go to `.glassbox/blobs/`, content-addressed and deduplicated. Only refs in SQLite.
- Redaction hooks run **before** persistence. Supplier names, costs, planner IDs configurable as confidential.
- Trace context propagates via `contextvars` — nested and async calls work without explicit passing.
- `GLASSBOX_ENABLED=0` disables all instrumentation with zero code change.
- `decision_context` owns all evidence emitted inside it. Nested decision contexts are rejected, and leaving a context without `complete()` discards its pending evidence with a rate-limited warning.
- `@gb.trace` supports both synchronous and asynchronous callables and preserves their return values and exception behavior.

### Collector delivery contract

- The writer queue is bounded and configurable. When full, it drops the newest Glassbox event, never blocks the agent, and emits a rate-limited warning plus a dropped-event counter. It marks the trace partial unless the trace's closing write has already committed; in that late-loss case the dropped-event counter and warning are the authoritative signals.
- `gb.flush(timeout=...)` waits for queued writes and reports success without raising. `gb.shutdown(timeout=...)` stops acceptance, performs the same bounded flush, and closes resources. An `atexit` handler performs best-effort shutdown.
- Partial traces are valid records and carry `status="partial"`; consumers must not silently treat them as complete.
- The first release persists a canonical internal event model. OTel export is a versioned translation layer added later; OTel convention changes must not alter the public SDK or stored event model.

---

## 7. Explainability: the Decision Card

The artifact a planner actually reads. Rendered by `explain/decision_card.py` into `web/templates/decision_card.html`, shared by the live server and the static exporter.

**Required sections:**

1. **Verdict** — recommendation in one line with a confidence band, not a bare number ("High — 0.84").
2. **What I looked at** — evidence table with source system, value, and **retrieval timestamp** so staleness is visible.
3. **Why** — rationale with inline references generated from `rationale_citations`; every citation resolves to evidence owned by this decision.
4. **What I ruled out** — `alternatives_considered`, with the reason each was rejected.
5. **Feedback control** — accept / modify / reject, writing to `overrides`. **This is the primary label-generation mechanism, not a UI nicety.**
6. **What would change my mind** — *P3.* Counterfactual threshold at which the recommendation flips. Hide this section in P2; once supported, show it only for agents with a valid counterfactual provider.

Enforce sections 2–4 structurally: empty evidence or alternatives fail eval, and every `rationale_citations` entry must resolve to evidence owned by the decision.

---

## 8. UI specification

**Stack:** FastAPI + Jinja2 + HTMX. One vendored `htmx.min.js`, one CSS file. No bundler, no node toolchain, and no client-side application framework through P5.

The Decision Card is a document with three buttons on it. HTMX handles the feedback POST in an attribute and swaps the control for a confirmation state. The same Jinja templates render both the live server and the static export — with React you would build the card twice.

### Routes (P0–P2)

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | **Decision Queue** — filterable list: agent, date range, decision_type, confidence band, override status. Sort by decided_at or confidence. This is the session entry point; nobody arrives at a card by URL. |
| `/decision/<id>` | GET | **Decision Card** — the §7 sections. |
| `/decision/<id>/feedback` | POST | HTMX target. Writes `overrides`, returns the confirmed control partial. |
| `/trace/<id>` | GET | **Trace View** — span tree with timings, tokens, cost, blob refs. For debugging, not for planners. |
| `/healthz` | GET | Liveness. |

Eval results and drift charts are **not** live-server routes. They belong in the P5 generated dashboard.

The three user-facing P2 screens are the Decision Queue, Decision Card, and Trace View. Feedback and health are supporting endpoints, yielding five routes total.

Planner feedback bypasses the fail-open telemetry collector. The feedback route writes synchronously through `store/` in a transaction and returns success only after commit. Constraint, lock-timeout, or persistence failures return an error state to the planner; an acknowledged feedback action must never be silently dropped.

### Local-server security

- Bind to `127.0.0.1` by default. Binding another interface requires an explicit `--host` argument and warning.
- Validate `Host` and `Origin` headers and require a CSRF token for every feedback POST.
- Escape rationale, evidence, alternatives, and planner-entered text by default. Blob contents are displayed as text and never interpreted as executable HTML.
- Apply conservative request-size limits to feedback fields.
- Require an idempotency key on feedback POSTs. Repeated delivery returns the original successful result without adding an event.
- Local-only does not imply trusted browser input; these controls apply even without an authentication model.

### Commands

```
glassbox serve --port 8787        # live server, read + write
glassbox export --decision <id>   # static self-contained HTML, read-only
glassbox export --queue --since 7d
```

Static export exists for emailing a card to someone outside the loop, attaching evidence to a review, or archiving. It is read-only **by design** — the feedback control renders as a disabled state with a link back to the live URL.

Static exports contain inline CSS, no JavaScript, escaped untrusted values, and no raw prompt/completion bodies unless explicitly requested. They may include the originating live URL, but mutation controls remain disabled.

The queue defaults to the last seven days, newest first, and uses cursor pagination. It provides explicit empty and error states; filters preserve pagination state.

### Contingency

If localhost binding requires a security exception you can't get: static cards where the feedback control triggers a JSON file download, reconciled by a watched-folder adapter in `adapters/file_csv.py`. Ugly. Only under constraint. **Verify port binding before P2 starts.**

---

## 9. Eval engine

### Suite definition (YAML)

```yaml
suite: replenishment_triage_v1
version: 1
target: integrations.replenishment_triage:run_case
golden_set: goldens/replenishment_triage/manifest.yaml
assertions:
  - name: schema_valid
    type: deterministic
    check: recommendation matches JSONSchema(schemas/triage_rec.json)
  - name: no_hallucinated_fields
    type: deterministic
    check: every rationale_citations ID resolves to evidence owned by the decision
  - name: evidence_present
    type: deterministic
    check: len(evidence) >= 3
  - name: alternatives_present
    type: deterministic
    check: len(alternatives_considered) >= 1
  - name: reasoning_quality        # P3
    type: llm_judge
    rubric: rubrics/triage_reasoning.md
    scale: 1-5
    threshold: 3.5
  - name: urgency_agreement
    type: labeled
    compares: recommendation.urgency vs case.expected_urgency
    metric: weighted_kappa
gates:
  block_merge_if:
    - deterministic_pass_rate < 1.0
    - urgency_agreement < 0.6
    - cost_per_decision_usd > 0.12
```

P1 ships the four deterministic assertions plus `urgency_agreement`. The LLM judge is P3.

### Evaluation target contract

Every suite declares an importable target implementing:

```python
class EvaluationTarget(Protocol):
    def run_case(self, case: GoldenCase) -> DecisionResult: ...
```

`GoldenCase` contains a stable case ID, typed input payload, expected labels, and metadata. `DecisionResult` contains the structured decision plus its evidence, citations, operational measurements, and any execution error. The runner loads the target, executes cases in isolated trace contexts, and records errors as failed results rather than aborting the suite. Suite versions, target agent versions, rubric versions where applicable, and golden-set content hashes are persisted with each run.

First-party adapters live in the repository's `integrations/` package. An agent may instead own its adapter when independently versioned, provided the suite's import path resolves in the evaluation environment.

### Metric set

| Layer | Metrics | Phase |
|---|---|---|
| **Correctness (immediate)** | assertion pass rate, hallucinated-field rate, schema validity | P1 |
| **Agreement (labeled)** | weighted kappa vs. expert labels, urgency confusion matrix | P1 |
| **Operational** | p50/p95 latency, cost per decision, token efficiency, error rate | P1 |
| **Quality (judged)** | reasoning rubric score, rationale–evidence consistency | P3 |
| **Real-world (deferred)** | override rate, precision on flagged exceptions, recall vs. realized stockouts | P4 |

### LLM-judge discipline (P3, but design for it now)

- Judge model differs from the agent model where possible.
- Calibrate against ≥30 human-labeled cases; report judge–human agreement. Refuse to gate on a rubric below κ = 0.6.
- Judge sees evidence + rationale, **not** the agent's confidence score (anchoring).
- Version rubrics. A rubric change invalidates historical comparability and bumps the suite version.

### Golden set construction

40–60 cases per agent. Composition target: 40% routine, 30% genuinely ambiguous, 20% adversarial (missing data, stale evidence, contradictory sources), 10% "correct answer is do nothing." That last bucket is the one everyone skips and it's where reasoning agents most often over-trigger.

---

## 10. Build phases

| Phase | Deliverable | Acceptance criteria |
|---|---|---|
| **P0 — Trace** | Event contracts, SDK, bounded collector, full SQLite schema/migrations, blob store; wrap one agent | Full trace tree persisted; agent unchanged with `GLASSBOX_ENABLED=0`; delivery behavior and overhead benchmark pass. Produce `docs/p0-ergonomics-report.md`. **STOP for explicit approval before P1.** |
| **P1 — Eval** | Evaluation-target adapter, typed golden cases, suite runner, 4 deterministic assertions, labeled agreement metric, 40-case golden set, CLI | `glassbox eval --suite replenishment_triage` returns per-assertion breakdown and a CI-compatible non-zero exit on gate failure; execution errors become case failures. Repository-provider workflow wiring is deferred to P5 |
| **P2 — Card + Server** | Three user-facing screens plus feedback and health endpoints, Jinja/HTMX templates, secure append-only feedback, static export | Structured planner usability check passes; feedback persists once under duplicate delivery; citations resolve and untrusted content is escaped |
| — **first release ends here** — | | |
| **P3 — Judge, Drift, Counterfactual** | LLM judge + calibration report; PSI/CUSUM monitors; counterfactual section; Discord alerts | Judge–human κ ≥ 0.6 reported; drift alert fires on injected synthetic shift |
| **P4 — Outcomes** | Outcome ingest + reconciliation; deferred precision/recall | Decisions ≥30 days old carry labels; override rate reported by decision_type |
| **P5 — Dashboard + CI** | Self-contained HTML dashboard; GitHub Actions gate | Dashboard opens offline; PR blocked on regression against baseline |

**Do not integrate a second agent until P2 ships.** The second integration is the real test of whether the SDK is non-invasive — expect one refactor at that point and plan for it.

---

## 11. Explicit non-goals (first release)

- No multi-tenant server, no auth model. Local, single-team.
- No live agent orchestration or routing. Glassbox observes; it does not execute.
- No automated prompt optimization. Report regressions, don't fix them.
- No real-time streaming. Batch refresh is sufficient and far cheaper.
- No client-side application framework through P5. Reconsider only from measured interaction requirements.

---

## 12. Risks

| Risk | Mitigation |
|---|---|
| Golden set goes stale; evals stay green while quality drops | Quarterly refresh; auto-promote cases where a planner override contradicted a passing eval |
| Evidence capture skipped because it's tedious | `evidence_present` is a blocking assertion from P1 |
| Instrumentation overhead in prod | Async buffered writer, sampling config, hard fail-open |
| Localhost bind blocked by IT | Verify **before** P2; §8 contingency is the fallback |
| Outcome labels never arrive (upstream data politics) | P4 depends on one CSV export. Scope that conversation before P4 starts, not during |
| Monolith calcifies | Import-linter contracts in CI from P0 |
| Evidence leaks between batched decisions | Explicit `decision_context`; reject nesting; discard incomplete pending evidence |
| Local feedback endpoint is targeted from another page | Loopback binding, Host/Origin validation, CSRF protection, escaping, request limits |
| Duplicate or revised feedback corrupts metrics | Idempotency keys plus append-only superseding override events |
| Content-addressed blobs grow without bound | Retention and garbage collection are explicitly deferred; monitor `.glassbox/blobs/` disk use and add policy when observed volume requires it |

### Acceptance-test details

**P0 overhead:** benchmark a documented representative integration workload after warm-up, with at least 30 baseline and 30 instrumented runs. Report median and p95. The pass condition before flush is `overhead_ms < max(0.05 × baseline_ms, 25 ms)` per decision. Report flush time separately. The ergonomics report records the exact integration diff and line count, public SDK calls used, agent-logic changes, disabled-mode write count, observed overhead, dropped events, and recommended API changes.

**P2 usability:** give five representative cards to at least one actual target planner using a structured checklist. The user must identify the verdict, top two evidence items, rejected alternative, and evidence timestamp without developer assistance, with at least 90% task completion. Also test accept, modify, reject, superseding a response, and duplicate POST delivery.

---

## 13. Kickoff prompt for Claude Code

> Read `glassbox-spec-v1.2.md`. Implement **Phase P0 only**, then stop and report.
>
> Create only the P0 modules from the repo scaffold in §4, the complete SQLite schema per §5 (all tables including `overrides` and `outcomes` — unused in P0 but required for forward compatibility), the canonical events and SDK per §6, and the import-linter contracts from §3. Do not create empty future-phase packages.
>
> Write pytest coverage for: trace context propagation across nested and async calls, fail-open behavior when the collector is unavailable, blob deduplication by content hash, redaction hook execution order, and `GLASSBOX_ENABLED=0` producing zero writes.
>
> Then wrap the agent at `/Users/MKunta/AGENTS/CODE/AI-driven-replenishment-exception-triage-agent` using the pattern in §6 and produce a sample trace tree at `examples/sample_trace.json`. If that path is unavailable, resolve and report the integration target before changing code.
>
> Run the §10 P0 overhead benchmark and write `docs/p0-ergonomics-report.md` with the required evidence. **Stop before P1 and request explicit approval.** Do not begin the eval engine or web app.
