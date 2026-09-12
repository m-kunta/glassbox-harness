# P2 Planner Usability Check

## Facilitator setup

Use a non-production Glassbox database containing at least five representative
decisions. Start the local server with a temporary access token and an operator
label, then give the participant the login URL and token only:

```shell
export GLASSBOX_DATABASE=/absolute/path/to/usability.sqlite3
export GLASSBOX_LOCAL_ACCESS_TOKEN='a-temporary-token-of-at-least-32-characters'
export GLASSBOX_OPERATOR_NAME='planner-usability-participant'
glassbox serve --port 8787
```

Open `http://127.0.0.1:8787/login`. Do not explain the Decision Card while the
participant completes the tasks. Record success only when they identify the
requested information unaided; record uncertainty or hints in Notes.

## Participant script

For each of five cards, ask the participant to identify the verdict, two
evidence items, one rejected alternative, and the evidence timestamp. Then ask
them to perform one operational action across the session: accept a decision,
modify a decision with replacement JSON, reject a decision, supersede one of
their earlier actions, and resubmit one identical action to verify duplicate
delivery does not create another history row.

P2.4 requires one target planner to review five representative Decision Cards.
For each card, without developer assistance, record whether the participant
identified the verdict, two evidence items, one rejected alternative, and an
evidence timestamp. P2.4 passes when at least 90% of the 20 tasks succeed.

| Card | Verdict | Two evidence items | Rejected alternative | Evidence timestamp | Notes |
| --- | --- | --- | --- | --- | --- |
| 1 | Pending participant session | Pending | Pending | Pending | |
| 2 | Pending participant session | Pending | Pending | Pending | |
| 3 | Pending participant session | Pending | Pending | Pending | |
| 4 | Pending participant session | Pending | Pending | Pending | |
| 5 | Pending participant session | Pending | Pending | Pending | |

Result: pending a real target-planner session. The source specification's
operational-action scenarios are now part of this P2.5 session. Record each
result below; P2 passes when at least 18 of the 20 comprehension tasks succeed
and all five operational actions are completed without unexpected behavior.

Pre-session interface review: confirm the queue identifies each decision by
type and entity, and every Decision Card visibly exposes its action, rationale,
readable evidence, and alternatives before recruiting the target planner.

| Operational task | Decision card | Result | Notes |
| --- | --- | --- | --- |
| Accept | Pending participant session | Pending | |
| Modify with corrected JSON | Pending participant session | Pending | |
| Reject | Pending participant session | Pending | |
| Supersede an earlier action | Pending participant session | Pending | |
| Resubmit an identical action | Pending participant session | Pending | |
