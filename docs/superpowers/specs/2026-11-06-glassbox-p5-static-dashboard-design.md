# P5 Static Operations Dashboard Design

**Date:** 2026-11-06  
**Status:** Draft for user review  
**Scope:** one offline, self-contained operational HTML report generated from
the existing Glassbox database.

## Goal

Give a local operator one portable view of the currently recorded operational
signals without starting the web server, granting a browser session, contacting
a network service, or changing database state.

The first increment answers three questions:

1. Which agent cohorts have healthy, watch, detected, or insufficient-data
   drift signals now?
2. What does recorded outcome reconciliation say about evaluated decisions?
3. What are the most recent counterfactual-run outcomes and their provenance?

It is an audit snapshot, not a live dashboard. Regenerating it is the explicit
refresh operation.

## Non-goals

- A FastAPI route, web session, login, live polling, HTMX, JavaScript, or a
  browser-resident application.
- Decision Card, trace, evidence, prompt, completion, blob, feedback, or
  override navigation and content.
- Scheduler health probes: launchd state is OS-owned rather than database-owned
  and remains the concern of `glassbox schedule status-drift`.
- Discord webhook configuration, delivery retries, or any outbound request.
- CI enforcement, policy changes, new drift calculations, or telemetry writes.
- PDF, ZIP, email, hosted, multi-file, or incremental exports.

## Command and artifact

Add one command using the existing top-level database selection convention:

```text
glassbox --database /absolute/path/glassbox.sqlite3 dashboard \
  --output /absolute/path/glassbox-dashboard.html
```

`--database` continues to default to `GLASSBOX_DATABASE` or
`glassbox.sqlite3`, exactly as the existing `export` command does. `--output`
is required. Generation refuses to replace an existing output unless
`--overwrite` is supplied. Successful generation exits `0`; invalid input,
missing/incompatible database, or unsafe output handling exits `2` with a
generic error that contains no database path, SQLite error, credential, or
content value.

The command opens the database through `Database.open_read_only()` exactly
once. It captures one UTC `as_of` instant immediately before opening that
connection, begins one explicit read transaction before its first source query,
and uses that transaction and `as_of` for every live drift calculation. It
never initializes, migrates, persists a drift run, claims an alert delivery, or
otherwise writes.

The output is UTF-8 HTML with embedded CSS and no `<script>`, external URL,
form, CSRF field, live-server URL, or mutable control. The writer uses a
same-directory private temporary file, flushes and fsyncs it, then atomically
replaces the requested target only after all rendering succeeds. A failed
generation never leaves a partial target behind and never overwrites a
pre-existing target without `--overwrite`.

## Content and source-of-truth rules

The report has a compact header followed by three self-contained sections.
All display content is autoescaped. Report IDs are plain audit references, not
links. Lists use explicit deterministic ordering so identical data and `as_of`
produce identical section order.

### Header

The header shows `Generated at` (the captured UTC instant), the report format
version, and a concise statement that the file is a read-only static snapshot.
It never shows the local database path or machine username.

### Drift overview

For every known agent cohort (the same decision-or-trace-derived cohort set
shown by `/drift`), sorted by `agent_name`, calculate one non-persisted live
drift report using the checked-in default drift policy and the shared `as_of`.
For each cohort show aggregate status, policy version and hash, baseline ID or
`Not materialized`, monitored signals, and the per-signal status/value/threshold
summary already defined by the drift presentation model. Disabled policy
signals remain visibly `Not monitored (disabled by policy)`.

An empty cohort set renders `No recorded decision or trace activity.` It is a
normal empty state. A policy or persisted-baseline integrity failure makes the
drift section `Unavailable`; the file records no underlying exception text.
This is distinct from a valid `insufficient_data` report, which preserves its
existing operator guidance.

### Outcome reconciliation

Calculate a read-only reconciliation report from recorded outcomes under the
checked-in `reconciliation_v2` policy. Show policy version/hash, total labelled
decisions, eligible count, unlabelled/maturity context, aggregate confusion
matrix totals, and precision/recall/F1 when their denominators exist. Include
the existing decision-type breakdown in deterministic decision-type order.

No outcome rows is a normal state: `No outcomes have been imported.` It must
not be presented as evidence that no negative outcomes occurred; the section
states that negative outcomes must be exported and imported explicitly.

### Counterfactual history

Show the most recent persisted counterfactual runs, newest first with run ID as
the deterministic tie-breaker. The first increment caps the visible list at
the newest 20 runs and states the cap when older runs exist. Each entry shows
run ID, creation instant, provider, model, prompt version/hash, execution mode,
selection/success/failure counts, and outcome-policy version/hash. It does not
show prompt text, model response/rationale, captured input, source decision
content, provider credentials, base URLs, or blob references.

No persisted run is a normal state: `No counterfactual runs have been
recorded.` A malformed run record makes this section `Unavailable` without
raw exception detail.

The first increment deliberately omits Discord delivery history. Delivery is
an optional side effect of a drift command, and a failed/disabled webhook must
not be mistaken for the truth of the drift report. Operators use the existing
command output and scheduler logs for delivery troubleshooting.

## Architecture

Create a static-dashboard boundary that is separate from the FastAPI server:

- `glassbox.dashboard` owns one-connection snapshot assembly, generic normal
  section states, Jinja rendering, and safe atomic output publication.
- It may reuse existing typed drift, reconciliation, and counterfactual report
  contracts plus static-display helpers from `glassbox.web.read_models`; it
  must not import `glassbox.web.server`, `auth`, `sdk`, `collector`, a provider
  adapter, Discord transport, or the macOS scheduler.
- `glassbox.cli` owns only `dashboard` argument parsing, lazy import, safe
  exit handling, and no content-bearing console output.
- A dedicated `dashboard.html` template and the existing CSS source form one
  inline artifact. Templates receive typed dashboard display models only, never
  raw `sqlite3.Row` objects or arbitrary repository mappings.

Add import-linter coverage that prevents runtime instrumentation, the live web
server, and provider/transport modules from importing the dashboard boundary.
The dashboard's one-way dependencies must remain `cli → dashboard →
store/eval/read-models`.

## Error and consistency policy

The explicit read transaction on the shared read-only connection provides one
consistent SQLite snapshot for all source queries. A missing database,
unsupported schema, connection failure, rendering failure, or output
publication failure fails the command safely without output creation and
preserves any pre-existing target. Normal absence is rendered locally in its
relevant section; unexpected malformed data or a calculation integrity failure
renders only that section as unavailable when the remaining sections can still
be assembled safely.

Every source value that reaches HTML is passed through Jinja autoescaping.
Sensitive operational content is excluded by model construction, rather than
redacted late in the template.

## Verification

Tests prove that:

- generation opens a strict read-only database and performs no write,
  migration, drift-run persistence, delivery claim, scheduler invocation, or
  provider call;
- the shared `as_of` is applied to every cohort and report ordering is stable;
- complete, empty, insufficient-data, disabled-signal, and unavailable section
  states render accurately;
- reconciliation and counterfactual summaries show the specified provenance
  and cap, while prompt bodies, rationales, blobs, URLs, credentials, feedback,
  and decision content never appear;
- untrusted agent names, policy values, and persisted strings are escaped;
- output is a standalone document with embedded CSS and no external assets,
  forms, scripts, or live links;
- output parent creation, overwrite refusal, atomic replacement, and failure
  preservation behave safely; and
- CLI exits and import-linter boundaries match the design.

## Deferred work

Interactive charts, historical trend comparison, Decision Card/trace links,
filtering, multiple policies, scheduler state, Discord delivery history, PDF or
email distribution, GitHub Actions, and CI enforcement each require a separate
approved increment.
