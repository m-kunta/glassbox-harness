# Glassbox P2.3 Feedback Workflow Design

## Scope

P2.3 adds authenticated, append-only planner feedback to a Decision Card. It
does not change a decision, create an override, export data, or add remote
access. Feedback is a separate human-assessment ledger; overrides remain
operational planner actions.

## Persistence

Add a `feedback` table and matching migration/schema copy. Each row contains a
system-generated ULID `feedback_id`, a `decision_id` foreign key, required
`verdict` (`agree`, `disagree`, or `uncertain`), optional `reason_code`,
optional `free_text`, optional JSON `corrected_recommendation`, a strict UTC
`created_at` timestamp, and a unique opaque `idempotency_key`. Feedback is
append-only: no update or delete API is introduced, and multiple legitimate
feedback rows may reference one decision.

The repository owns the typed `FeedbackRecord` write and read methods. A
duplicate idempotency key returns the original row when its immutable request
payload matches; a key reused with different payload is rejected as a generic
client error. The write uses one SQLite transaction so no partial feedback row
can appear.

## Request flow and security

`GET /decision/{decision_id}` renders the existing Decision Card plus a POST
form with an opaque idempotency key and the server-side session's CSRF token.
`POST /decision/{decision_id}/feedback` accepts the form only for an existing
decision and an authenticated session. It validates a constant-time CSRF token
comparison, request-size limits, allowed verdict, optional bounded strings,
and JSON-only corrected recommendation before writing.

The endpoint uses the existing loopback-only authentication/session boundary.
Every authenticated feedback attempt, including validation or persistence
failure, refreshes session activity. Failures render a generic escaped response
without raw SQLite errors, paths, idempotency-key state, or decision existence
details beyond the normal authenticated 404. Successful writes redirect back to
the Decision Card using PRG, preventing a browser refresh from resubmitting.

## Display and boundaries

The Decision Card displays feedback newest first, with verdict, optional reason
code, note, corrected recommendation JSON, and timestamp. Persisted values are
presentation models and Jinja-autoescaped. The web layer may import `store` but
never `sdk`; `store` never imports `web`.

## Verification

Tests must prove that:

1. feedback rows are append-only, strictly validated, and linked only to an
   existing decision;
2. identical replay returns one row, while a new idempotency key permits a
   second feedback row;
3. CSRF, authentication, payload limits, invalid JSON, and invalid verdicts
   reject before persistence;
4. successful POST uses PRG; authenticated failed POSTs refresh session
   activity; and generic errors do not disclose SQLite internals;
5. the Decision Card autoescapes and renders the feedback history; and
6. the complete suite, static checks, and import-linter contracts remain green.
