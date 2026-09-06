# Glassbox P2.2 read experience design

## Purpose

P2.2 turns persisted Glassbox decisions into authenticated, planner-readable
pages without adding feedback mutation or static export. It replaces P2.1's
protected root placeholder with the decision queue and adds read-only Decision
Card and trace routes.

## Scope

P2.2 creates read-only web view models, repository reads, Jinja templates, and
the queue/card/trace routes. It does not add feedback submission, CSRF POST
enforcement, HTMX behavior, static export, blob-content display, or planner
usability testing; those remain P2.3–P2.4 work.

## Read-only database boundary

`Database.open_read_only(path)` is a separate factory from `Database.open()`.
It requires an existing database file and opens its resolved URI with SQLite
`mode=ro`. It sets row access and a bounded busy timeout, but never creates a
directory or database file, changes journal mode, initializes schema, runs a
migration, commits, or writes application data.

The factory validates the schema objects against Glassbox's current strict
released schema. A pre-strict database raises a clear error telling the
operator to run a writer-capable Glassbox session first; an unknown schema
raises a clear unsupported-schema error. `glassbox serve` preflights this
factory and closes the result before opening its listener, so a missing,
legacy, or unsupported database is a startup error.

The web server is a separate WAL reader from the agent and collector writer.
Every HTTP request opens and closes its own read-only `Database` and
`Repository`; no SQLite connection is shared through `app.state`. This keeps
FastAPI's request threads independent while preserving visibility of
committed-but-uncheckpointed WAL data. A separate-process integration preflight
must prove this behavior on the target operating system using a scratch writer
and reader: the reader sees a writer's committed record before checkpoint and
also sees a record committed after the reader's first read. If the host's
directory permissions prevent required WAL sidecar coordination, startup is
unsupported and the operator must correct them rather than falling back to a
non-WAL or mutable read path.

## Components and boundaries

- `glassbox.store.database`: adds `Database.open_read_only()` and its
  read-only schema checks. It remains SQLite-aware and imports no web package.
- `glassbox.store.repository`: adds parameterized decision queue, decision,
  trace, and effective-override reads. It accepts typed filter bounds and sort
  columns only; it never imports web display concepts.
- `glassbox.web.read_models`: immutable presentation data for queue rows,
  Decision Cards, evidence groups, citation references, override states, trace
  nodes, and page diagnostics.
- `glassbox.web.read_service`: transforms typed repository records into read
  models. It owns generic recommendation formatting, confidence bands,
  citation-to-evidence grouping, override diagnostics, and span-tree shaping.
- `glassbox.web.server`: loads `GLASSBOX_DATABASE` from the process environment
  with default `glassbox.sqlite3`, preflights it at startup, then registers
  authenticated read routes. It may import `store`, never `sdk`.
- `glassbox.web.templates`: receives only read models; Jinja autoescaping is
  mandatory for every persisted value.

## Queue query contract

`GET /` renders the queue, newest first by default. It supports exact agent and
decision-type filters; optional `from` and `to` dates in `YYYY-MM-DD` format,
interpreted as inclusive UTC calendar dates; confidence-band filtering;
read-only override-status filtering; and sort choice. The service maps `to` to
the following UTC midnight as an exclusive SQL bound. Accepted override-status
values are `none`, `accepted`, `modified`, `rejected`, and `inconsistent`.

Cursor pagination preserves all active filters. A cursor is a base64url-encoded
JSON object containing the selected sort value and `decision_id` tie-breaker.
The service validates its exact keys and value types, then passes both as bound
values; malformed cursors are validation errors. This makes each page boundary
stable when several decisions share one timestamp or confidence value.

Request values are always bound parameters. Sort input is never interpolated:
the only accepted values are `timestamp` mapped to the literal SQL identifier
`decided_at` and `confidence` mapped to `confidence`; an unknown value is a
validation error. Repository query methods receive the selected fixed column,
not the raw request string.

`ConfidenceBand` is a single web-owned definition used both by the display
formatter and by the read service's filter-bound construction:

| Band | Range |
| --- | --- |
| Low | `0.00 <= confidence < 0.50` |
| Medium | `0.50 <= confidence < 0.80` |
| High | `0.80 <= confidence <= 1.00` |

The repository receives explicit numeric lower/upper bounds and does not
import this web type. P2.2 accepts the existing composite decision indexes and
whole-queue newest-first sort at local single-operator scale; a standalone
`decided_at` index requires measured need before addition.

## Generic display semantics

Glassbox remains agent-agnostic. A recommendation is displayed as canonical,
key-sorted compact JSON. Queue rows truncate this string after 160 Unicode
characters and append `…`; the Decision Card uses the full one-line value.
The card verdict shows this value with its confidence-band label and exact
numeric confidence.

An evidence citation key identifies a group, not one field. The read service
groups each decision's evidence by `evidence_id`, preserving fields in
repository order. Each rationale citation resolves to its full field group and
uses a positional HTML anchor generated by the service, never raw caller input.
If a citation has no group, the card renders a visible unresolved-citation
diagnostic. It never silently drops the citation or fails the entire card.

The current override is the unique override row for a decision that no other
override's `supersedes_override_id` references. No rows means no current
override. Exactly one head is current. Zero heads with one or more rows, or
more than one head, is an inconsistent override-history diagnostic. This makes
self-referencing and branching histories observable instead of choosing an
arbitrary timestamp winner.

The trace view converts spans to a stable, time-ordered tree. A span whose
parent is absent from the trace or whose parent relationship cannot be placed
is displayed as an orphan diagnostic rather than silently removed. It shows
span name, kind, timing, model/token/cost fields when present, and prompt or
completion blob references only; it never opens blob contents.

## Routes and templates

All routes retain P2.1's local-token session guard.

- `GET /`: `queue.html` renders a filter form, queue rows, current override
  state or diagnostic, cursor controls, and explicit empty state.
- `GET /decision/{decision_id}`: `decision_card.html` renders the verdict,
  evidence groups with retrieval timestamps, rationale citations, unresolved
  diagnostics, alternatives considered, and a disabled notice that feedback
  arrives in P2.3. A missing decision is a generic `404`.
- `GET /trace/{trace_id}`: `trace.html` renders trace metadata and the span
  tree. A missing trace is a generic `404`.

Shared layout and error templates include authenticated navigation to the
queue. If a previously validated database becomes unavailable during a request,
the server renders a generic `503`; it never renders SQLite exception text or a
filesystem path. Persisted recommendations, rationale, evidence values,
alternatives, override data, agent fields, and blob references are passed as
data and autoescaped by Jinja.

## Verification

Tests must prove:

1. a read-only open neither creates a missing database nor attempts DDL,
   migration, journal-mode changes, or data writes;
2. strict schema opens read-only; pre-strict and unknown schemas fail with
   actionable errors;
3. a separate writer process and read-only reader process exchange committed
   WAL records before checkpoint and after a later writer commit;
4. queue filters bind values, sort choices use only the two fixed columns, and
   unknown sort/filter syntax fails validation;
5. all confidence-boundary values have one band and produce matching filter
   bounds;
6. evidence groups contain every field under one citation key, dangling
   citations are visible, and anchors do not derive from caller-defined IDs;
7. normal override chains show one head, while multiple heads and a
   self-referencing zero-head history show an inconsistency diagnostic;
8. authenticated queue/card/trace routes render escaped persisted content,
   empty state, and generic `404`/`503` behavior; and
9. `lint-imports` keeps `web-dependencies` enforced with no `web -> sdk`
   import.

## Deferred decisions

P2.3 defines feedback request sizes, Host/Origin validation, CSRF enforcement,
idempotency, synchronous writes, and the operational rule for whether failed
feedback writes refresh session activity. P2.4 defines static export and the
planner usability evaluation. No P2.2 route writes to SQLite.
