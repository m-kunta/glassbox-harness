# P2 Planner Usability Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax.

**Goal:** Provide a safe, repeatable facilitator package for a future target-planner P2 usability session without recording a fabricated result.

**Architecture:** Keep docs/p2-planner-usability.md as the single canonical checklist and result record. Add one standalone facilitator invitation with placeholders that directs the facilitator to that checklist; no application, database, token, or test data changes are needed.

**Tech Stack:** Markdown, repository documentation checks, git.

## Global Constraints

- Do not mark P2.4 complete or fill any participant-result cells before an actual target planner performs the session.
- Include placeholders only: never commit a real access token, database path, participant identity, customer data, or production identifier.
- Preserve the exact pass rule: at least 18 of 20 unaided comprehension tasks and all five operational actions complete without unexpected behavior.
- The participant receives only the local login URL and temporary token; the facilitator does not coach the interface.
- The checklist remains the one canonical result record.

---

### Task 1: Add a facilitator invitation and cross-reference it from the checklist

**Files:**
- Create: docs/p2-planner-usability-invitation.md
- Modify: docs/p2-planner-usability.md
- Verify: TODO.md

**Interfaces:**
- Produces a copyable facilitator message with placeholders <LOCAL_LOGIN_URL> and <TEMPORARY_ACCESS_TOKEN>.
- Produces a checklist link to the invitation and a clear statement that P2.4 is pending until the checklist records a real session.
- Does not change runtime code or the TODO completion state.

- [x] **Step 1: Write the failing documentation assertions**

Run the following checks before adding the invitation:

~~~bash
test -f docs/p2-planner-usability-invitation.md
rg -n '<LOCAL_LOGIN_URL>|<TEMPORARY_ACCESS_TOKEN>' docs/p2-planner-usability-invitation.md
~~~

Expected: both commands fail because the handoff invitation does not exist.

- [x] **Step 2: Create the copyable facilitator invitation**

Create docs/p2-planner-usability-invitation.md with these exact sections:

~~~markdown
# P2 Planner Usability Session Invitation

## Facilitator message

Hello,

Please use Glassbox to review five recorded decisions at:

<LOCAL_LOGIN_URL>

Temporary access token:

<TEMPORARY_ACCESS_TOKEN>

For each decision, tell me the recommended action, two evidence items, one alternative that was ruled out, and the evidence timestamp. Please work without guidance about the interface.

During the session, complete these operational actions once each: accept, modify with valid replacement JSON, reject, supersede an earlier action, and resubmit one identical action.

## Facilitator instructions

- Use a temporary non-production database and token.
- Do not explain the card layout or controls.
- Record all answers and operational results in docs/p2-planner-usability.md.
- Do not include the participant's identity, the real token, or production data in the committed result record.

## Pass rule

P2.4 passes only after an actual target planner completes at least 18 of 20 comprehension tasks unaided and all five operational actions without unexpected behavior.
~~~

- [x] **Step 3: Link the checklist to the invitation**

Under the Facilitator setup heading in docs/p2-planner-usability.md, add:

~~~markdown
Use the copyable [facilitator invitation](p2-planner-usability-invitation.md)
to invite the participant. Replace its two placeholders only outside version
control; never commit them.
~~~

Retain every Pending participant session cell and the existing P2.4 unchecked TODO item.

- [x] **Step 4: Verify the handoff package**

Run:

~~~bash
rg -n '<LOCAL_LOGIN_URL>|<TEMPORARY_ACCESS_TOKEN>|without guidance|18 of 20|all five operational' docs/p2-planner-usability-invitation.md
rg -n 'facilitator invitation|Pending participant session|18 of the 20|all five operational' docs/p2-planner-usability.md
rg -n '^\- \[ \] Run the structured planner usability test\.' TODO.md
git diff --check
~~~

Expected: invitation and canonical checklist contain the required protocol; TODO confirms P2.4 remains unchecked; no whitespace errors occur.

- [x] **Step 5: Commit**

~~~bash
git add docs/p2-planner-usability.md docs/p2-planner-usability-invitation.md
git commit -m "docs: prepare planner usability handoff"
~~~

## Plan Self-Review

- **Spec coverage:** Task 1 adds the facilitator message, preserves the canonical result record, repeats the no-coaching and data-handling rules, and verifies the fixed pass condition.
- **Placeholder scan:** the only placeholders are deliberate safe runtime substitutions in the invitation; no implementation uncertainty remains.
- **Scope:** documentation-only; it does not alter the UI, server, schema, or P2.4 status.

