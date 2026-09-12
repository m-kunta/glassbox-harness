# Glassbox P2 Readable Decision Brief Design

## Scope

This repair makes the existing P2 queue and Decision Card understandable to a
planner without adding agent-specific semantics or changing persisted Glassbox
events. It fixes the current leak of Python `FrozenDict` representations into
HTML and replaces the bare data-table presentation with a compact, readable
Decision Brief.

The repair does not add replenishment-specific labels, change the event schema,
or alter feedback and operational-override persistence.

## Presentation boundary

`glassbox.web.read_models` remains the sole typed-to-display boundary. It
creates JSON-safe display values from event data before templates receive them;
Jinja templates never render `FrozenDict`, event-model mappings, or tuples
directly.

Every nested value uses canonical JSON formatting. Objects and arrays render in
formatted JSON blocks; primitive values render as plain text. This preserves
agent-neutral fidelity without making a planner read Python object
representations.

The Decision Card derives only contract-level facts:

- decision type;
- `entity_type` and `entity_id`;
- a recommended action when the opaque recommendation has a string `action`
  member, otherwise a neutral `Not specified` label;
- confidence band and numeric confidence;
- rationale;
- formatted full recommendation; and
- evidence, alternatives, feedback, and override history.

Alternatives are shown individually. If an alternative mapping has `action` and
`reason` string members, it renders as a readable rejected-action statement;
otherwise it renders as formatted JSON. Evidence groups preserve their
caller-supplied citation key while formatting each field value safely.

## Decision Brief and queue

The Decision Card leads with a Decision Brief: decision type, entity, recommended
action, confidence, and rationale. It then presents evidence, alternatives, and
the full recommendation in visually distinct sections. Operational override and
feedback remain below the decision information. Their forms use vertically
stacked, explicitly labelled controls and short help text.

The queue identifies a row primarily by decision type and business entity. The
ULID remains available as subdued metadata for traceability, not the primary
planner-facing label. The queue continues to show the generic recommendation
summary, confidence, and current override status.

`base.html` owns a small local stylesheet shared by live pages. It uses no
external assets or frontend framework. The CSS provides readable hierarchy,
spacing, responsive overflow handling for tables and JSON, and visually clear
status/form sections.

## Attribute presentation

Opaque mappings that contain only scalar values are displayed as readable
attributes rather than compact JSON. Keys become title-cased labels with
underscores replaced by spaces. Booleans become `Yes` or `No`; `null` becomes
`Not provided`; strings and numbers keep their underlying value.

The `action` member, when present as a string, is the primary recommendation
label. Numeric values retain their literal representation; Glassbox does not
infer units or display conventions from an opaque field name.

The queue shows the primary action plus secondary attributes. The Decision Card
uses the same attributes for evidence and the full recommendation. Mappings
with nested objects or arrays retain formatted raw JSON under a disclosure,
ensuring unknown agent payloads remain faithful and inspectable. Static exports
use the same display models and output.

## Verification

Tests prove that display models serialize nested mappings and alternatives with
no Python object representation, derive generic summary facts, and preserve
opaque unknown values through formatted JSON. HTTP-level tests assert the queue
and Decision Card render business entity/action/rationale content, readable
evidence and alternatives, and no `FrozenDict` or object-address text.

Tests additionally prove scalar attribute formatting, nested-value JSON
fallback, and consistent queue/card/export rendering of readable attributes.

After automated checks pass, the existing scratch usability session is reopened
in the browser for a visual review. The P2 planner usability check remains
pending a real participant; this repair makes that check viable but does not
claim its result.
