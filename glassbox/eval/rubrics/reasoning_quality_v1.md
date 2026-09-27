# Reasoning quality rubric — `reasoning_quality_v1`

This rubric scores the **decision reasoning** that Glassbox recorded for one
decision: its rationale, cited evidence, and considered alternatives. It does
not evaluate the recommendation's business outcome, and it never evaluates a
model's hidden chain-of-thought — only the reasoning that was actually
recorded and shown to the planner or judge as evaluable data.

A score is an integer from 1 through 5:

| Score | Meaning |
|---|---|
| 1 | The recommendation is unsupported by, or conflicts with, the cited evidence. |
| 2 | Material evidence, rationale, or alternative analysis is missing or materially flawed. |
| 3 | The recommendation is basically grounded in evidence, with understandable but meaningful gaps. |
| 4 | Evidence, rationale, and alternatives are clearly connected and operationally useful. |
| 5 | Reasoning is precise, complete for the decision, evidence-grounded, and candid about relevant limits. |

## Scope and provenance

- This version is `reasoning_quality_v1`. Every stored score is paired with
  the rubric version that produced it, so results from a future revision of
  this rubric are never compared directly against `reasoning_quality_v1`
  scores.
- A scorer — human planner or LLM judge — evaluates only the decision's
  recorded rationale, its cited evidence, and its considered alternatives. It
  is not shown, and must not infer or request, any hidden chain-of-thought
  behind those recorded artifacts.
- When an LLM judge applies this rubric, it should return a concise
  evaluative rationale explaining the score, not a hidden or extended
  chain-of-thought trace.
- A rubric edit is never made in place. Any change to these definitions
  creates a new versioned file (e.g. `reasoning_quality_v2.md`) and the
  persisted rubric version used going forward must be updated to match.
