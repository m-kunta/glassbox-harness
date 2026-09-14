# P2 Planner Usability Handoff Design

## Purpose

Prepare the remaining P2.4 structured usability gate for a future target-planner session without claiming that the session has occurred. The existing Glassbox UI and five-card scratch database are the system under test; this work adds only repeatable facilitator materials and a results-recording format.

## Scope

The handoff package consists of:

1. The existing checklist in docs/p2-planner-usability.md, retained as the canonical result record.
2. A concise facilitator message that shares only the loopback login URL and temporary token with the participant, instructs the facilitator not to coach, and gives the participant five comprehension prompts plus five operational actions.
3. A completion protocol: record one result per required task, preserve participant notes verbatim, calculate 20 comprehension tasks, and evaluate pass at 18 or more successful tasks plus completion of every operational action.

No source code, schema, UI behavior, evaluation gate, or planner result is changed.

## Session flow

1. The facilitator creates or selects a non-production database with five representative decisions, starts Glassbox with a fresh local access token and operator label, and verifies the queue’s pre-session interface.
2. The facilitator sends the participant the local URL and token only. They must not explain the decision-card layout, evidence labels, recommendation fields, or feedback controls.
3. For each card, the participant identifies the verdict, two evidence items, one rejected alternative, and an evidence timestamp. The facilitator records success only for unaided answers.
4. Across the five cards, the participant accepts one recommendation, modifies another using valid replacement JSON, rejects another, supersedes an earlier operational action, and submits an identical action again to prove idempotent delivery.
5. The facilitator records results and notes in the canonical checklist. The result is pass only if at least 18 of 20 comprehension tasks succeed and all five operational actions complete without unexpected behavior.

## Data handling and boundaries

Use a temporary, non-production database and temporary local access token. Do not put a real token, participant identity, customer data, or production identifiers into the checklist. Preserve only task results and participant-provided usability notes needed to explain failures.

## Verification

Before handoff, confirm the existing document includes:
- the exact facilitator setup command with placeholders;
- the five-card comprehension table;
- all five operational-action rows;
- the 18-of-20 plus five-actions pass rule;
- a clearly visible pending state.

After an actual session, the facilitator fills in those existing rows, appends the observed result, and only then updates the P2.4 TODO checkbox.

