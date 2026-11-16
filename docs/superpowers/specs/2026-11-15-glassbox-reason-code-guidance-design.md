# Reason-code guidance design

## Status

Approved for implementation planning on 2026-11-15.

## Problem

Both planner-facing forms accept an optional free-text reason code, but
Glassbox has no configured codebook. Browser autocomplete can display previous
numeric entries as though they were application-defined choices, leaving a
planner unable to determine their meaning.

## Scope

This increment applies the same small clarification to the Feedback and
Operational override forms.

- Set the reason-code inputs' `autocomplete` attribute to `off` to request
  that browsers do not offer unrelated prior entries.
- Add visible help directly below each field: reason codes are optional,
  Glassbox does not define their meanings, and any entered code must be
  explained in the adjacent Note or Comment.
- Preserve the current append-only free-text storage and display of existing
  reason codes without reinterpretation.

## Non-goals

- No codebook, dropdown, configuration file, schema migration, or code-value
  validation is introduced.
- Glassbox does not assign meaning to agent- or organization-specific codes.
- This change does not close the P2.4 human usability gate; it removes one
  usability obstacle for its next run.

## Verification

Template/HTTP coverage proves that both forms expose the same helper copy and
disable autocomplete. Existing feedback and override persistence tests remain
green, proving the form field names and payload shape are unchanged.

## Future extension

When an operator has a stable, reviewed codebook, a separate design may add a
versioned TOML policy that supplies labeled choices. That feature must preserve
free-text history and make its policy provenance explicit; it is deliberately
outside this increment.
