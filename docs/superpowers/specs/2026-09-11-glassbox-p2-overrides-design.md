# Glassbox P2.5 Operational Overrides Design

## Scope

P2.5 adds authenticated operational override actions to a live Decision Card:
`accepted`, `modified`, and `rejected`. It writes the existing append-only
`overrides` table and makes P2.2's current-override display live. It does not
replace the P2.3 assessment feedback ledger, add multi-user authentication, or
change the decision itself.

## Configuration and form

Startup reads `GLASSBOX_OPERATOR_NAME` through `os.environ.get`, defaulting to
`local-planner`. A configured value is stripped and must remain non-empty. The
value is captured once in `ServerConfig` and recorded as the `actor` for every
override; it is an accountability label, not multi-user identity.

The Decision Card renders one authenticated POST form with action, optional
reason code/free text, optional corrected recommendation JSON, CSRF token, and
opaque idempotency key. `modified` requires corrected recommendation JSON;
`accepted` and `rejected` reject it. Request limits match P2.3: action at most
16 characters, reason code at most 64, free text at most 4 KiB, and corrected
JSON at most 16 KiB.

The POST retains P2.3's exact Host rule, optional-but-matching Origin rule,
constant-time CSRF comparison, generic failures, session refresh behavior, and
303 Post/Redirect/Get response. It opens one short-lived writer connection.

## Atomic persistence

`Repository.record_override(submission)` owns current-head discovery and the
append-only write in one `BEGIN IMMEDIATE` transaction.

For a decision, it classifies override history as:

- no rows: append the first row with `supersedes_override_id = NULL`;
- exactly one unsuperseded head: append a row that supersedes that head; or
- existing rows with zero heads, or more than one head: raise an inconsistent
  history error without writing.

This reuses the same definition as the P2.2 read model: a head is an override
not referenced by another row's `supersedes_override_id`. The immediate write
transaction prevents concurrent valid submissions from reading the same head
and manufacturing a branch.

The existing unique `idempotency_key` protects exact replay. If it already
exists, the repository compares only caller-supplied payload fields:
`decision_id`, `action`, `modified_value`, `reason_code`, and `free_text`.
An exact match returns the original record; a changed payload rejects. Generated
`override_id` and `created_at` never participate in replay equality.

## Display and verification

The Decision Card displays the current override and complete newest-first
history. Templates autoescape all stored fields. Tests prove first action,
linear supersession, self-reference/multiple-head refusal, concurrent-write
serialization, exact idempotent replay, changed-payload rejection, modified
payload rules, operator configuration, Host/Origin/CSRF rejection, PRG, and
escaped display. P2.5 also owns the deferred operational-action portion of the
planner usability check: accept, modify, reject, superseding a response, and
duplicate POST delivery. A cross-layer regression writes a superseding override
through `record_override`, then asserts the queue SQL and Decision Card
graph-walk report the same current action and head selected by the write
transaction. This guards all three head-classification implementations against
drift.
