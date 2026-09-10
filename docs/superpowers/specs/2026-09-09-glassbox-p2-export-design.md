# Glassbox P2.4 Single-Decision Export Design

## Scope

P2.4 adds one read-only command:

```shell
glassbox export --decision <decision-id> --output <path>
```

It writes one static HTML Decision Card for email, review attachment, or
archival. It does not add queue export, ZIP packaging, trace export, blob-body
display, JavaScript, or any write path. Bulk queue export remains a separate
future design question because it needs explicit volume, layout, and packaging
decisions.

`export` uses the existing top-level `--database` CLI option, including its
`GLASSBOX_DATABASE`/`glassbox.sqlite3` default. Like `trace`, it is a
single-shot read-only command; it does not use the long-running server's
configuration path.

## Architecture

The CLI opens the configured database exclusively with `Database.open_read_only`
and obtains the card through `ReadService.decision_card()`. That preserves the
live UI's typed presentation rules: opaque recommendation formatting, evidence
grouping, dangling-citation diagnostics, current override state, append-only
feedback history, and escaped planner-provided text.

The exporter renders a dedicated `decision_export.html` template, rather than
putting a static-mode switch in the live Decision Card template. The template
uses inline copies of the local stylesheet and contains no JavaScript, form,
CSRF token, session cookie, or mutation endpoint. Its feedback section is
plain read-only history. When `--live-base-url` is supplied, it may include one
escaped link to the corresponding live Decision Card; without that option it
contains no live URL.

The output path is an explicit file path. The exporter creates only its direct
parent directory when needed, writes only that file, and never creates or
migrates a database. Existing output files are overwritten only with an
explicit `--overwrite` flag.

## CLI and errors

`glassbox export` accepts:

- `--decision` — required canonical decision ULID.
- `--output` — required HTML output file.
- `--live-base-url` — optional `http://127.0.0.1:<port>` or
  `http://[::1]:<port>` base URL, validated as loopback HTTP.
- `--overwrite` — required to replace an existing output file.

A missing decision, invalid identifier, unsupported/read-only database,
invalid output target, invalid live URL, or an existing output without
`--overwrite` returns a concise CLI error and a nonzero exit code. Errors never
include raw SQLite exceptions, database paths, tokens, or feedback keys.

## Safety and verification

Tests must prove that export:

1. reads an existing current-schema database without changing its main or WAL
   data;
2. writes a standalone HTML file with inline CSS and no external asset, form,
   script, CSRF, or feedback POST reference;
3. preserves card content and autoescapes hostile persisted feedback/evidence;
4. makes a supplied loopback live URL safe and optional;
5. rejects missing records, invalid input, and accidental overwrite; and
6. keeps the full test, lint, typing, and import-boundary suite green.

## Usability gate

After implementation, conduct the structured planner usability check with five
representative cards. The participant must identify the verdict, top two
evidence items, one rejected alternative, and an evidence timestamp without
developer assistance. Record task completion and observations in a durable
project artifact; P2.4 completes only when the result reaches at least 90%.
The source specification's accept/modify/reject, superseding-response, and
override-action usability scenarios are deferred to the P2.5 operational
override workflow, consistent with the 2026-09-08 separate-feedback-ledger
decision.
