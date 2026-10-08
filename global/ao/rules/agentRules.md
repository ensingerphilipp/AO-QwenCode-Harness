# AO worker rules

These rules apply to AO task workers. Global Qwen rules and repository-local project contracts remain applicable.

## Select exactly one mode

- **Implementation mode** is the default.
- **Reviewer mode** applies only when the assignment contains `AO_SEMANTIC_REVIEW`.
- Never combine modes. An implementation worker never reviews its own PR; a reviewer never changes implementation code.

## Task completion report

Worker-to-orchestrator communication uses directed AO messages. Every message to the orchestrator must use `ao send`; pane text and final assistant prose are not lifecycle handoffs.

- Resolve the active orchestrator ID from AO's current session context ("Orchestrator Coordination") at send time. Never hard-code or reuse a prior orchestrator session ID.
- Send lifecycle payloads with `ao send --session <ACTIVE_ORCHESTRATOR_ID> --message '<SEMANTIC_MESSAGE>'`.
- PR-bearing implementation tasks end with `READY_FOR_REVIEW`, `READY_FOR_REREVIEW`, or `REVIEW_HANDOFF_BLOCKED`.
- Reviewer verdict handoff is `SEMANTIC_REVIEW_RESULT` or `SEMANTIC_REVIEW_FAILURE`; a successful reviewer remains alive after `SEMANTIC_REVIEW_RESULT` for the required AO publish/discard continuation and finally ends with `SEMANTIC_REVIEW_PUBLISHED`, `SEMANTIC_REVIEW_DISCARDED`, or `SEMANTIC_REVIEW_PUBLICATION_FAILURE`.
- Every other task (freeform, host-level, read-only, no-change) ends with a generic completion report unless the task specifies a report token or exact format:

```text
ao send --session <ACTIVE_ORCHESTRATOR_ID> --message 'TASK_COMPLETE
{
  "workerSessionId": "<AO_SESSION_ID>",
  "summary": "<ONE_SENTENCE>",
  "changed": <true|false>
}'
```

- If the task specifies a report token or exact format (for example `SKILL_SYNC_DONE sha=... tests=...`), send exactly that semantic message instead of `TASK_COMPLETE`.
- The completion report is a mandatory task-lifecycle event; it is permitted under the general "message the orchestrator only for true blockers" guidance.
- The sole fallback exception is when the active orchestrator ID cannot be resolved. In that case, surface the blocked state in the current task, do not guess or hard-code a session ID, and stop.
- After sending a genuinely terminal report, stop working and remain available. A reviewer verdict handoff is intentionally nonterminal until AO resolves publish/discard.

## Implementation mode

1. Implement only the assigned scope and respect project-defined human-in-the-loop boundaries.
2. Run `bash scripts/verify`. Do not hand off while the required local gate fails.
3. For an implementation change intended for integration, commit and push the scoped change and create or update its pull request. Pull-request-based development is the harness baseline; do not invent a PR for a read-only/no-change task.
4. Resolve the canonical PR URL and exact 40-character head SHA for every PR-bearing handoff.
5. Send `READY_FOR_REVIEW` to the active orchestrator with `ao send`.

```text
ao send --session <ACTIVE_ORCHESTRATOR_ID> --message 'READY_FOR_REVIEW
{
  "workerSessionId": "<AO_SESSION_ID>",
  "prNumber": <NUMBER>,
  "prUrl": "<CANONICAL_URL>",
  "headSha": "<40_CHARACTER_SHA>",
  "localVerification": "bash scripts/verify: pass",
  "summary": "<ONE_SENTENCE>",
  "repairCycle": 0
}'
```

After sending the handoff, remain available and stop. The orchestrator owns deterministic CI qualification and, when enabled, semantic-review dispatch. Do not invoke `/ao-pr-review`, `/review`, or `qwen review run` yourself.

If no active orchestrator ID is available for a PR-bearing handoff, surface `REVIEW_HANDOFF_BLOCKED` in the current task and stop. Semantic-review-disabled projects still use the same exact-PR/SHA handoff so the orchestrator can qualify deterministic CI without dispatching semantic review.

### Routed fixes

- Act on `CI_FIX_REQUEST` only for an in-scope failure routed by the orchestrator. Apply the narrow repair, run `bash scripts/verify`, push, and send a fresh `READY_FOR_REVIEW` for the new head.
- Act on `REVIEW_FIX_REQUEST` only for the exact PR/reviewed SHA routed by the orchestrator. Inspect the **native Qwen GitHub review** and apply all eligible in-scope review fixes together. Do not depend on an AO paraphrase or second finding projection. Independently preserve assigned scope and project HITL boundaries.
- After semantic repair, run `bash scripts/verify`, push, and send `READY_FOR_REREVIEW`.

```text
ao send --session <ACTIVE_ORCHESTRATOR_ID> --message 'READY_FOR_REREVIEW
{
  "workerSessionId": "<AO_SESSION_ID>",
  "prNumber": <NUMBER>,
  "prUrl": "<CANONICAL_URL>",
  "previousReviewedSha": "<OLD_SHA>",
  "headSha": "<NEW_40_CHARACTER_SHA>",
  "localVerification": "bash scripts/verify: pass",
  "repairCycle": 1
}'
```

Never perform a second automatic semantic repair. If a routed request would require scope expansion, a protected project decision, or uncertain judgment, send `NEEDS_HUMAN` to the active orchestrator with `ao send` instead.

## Reviewer mode: persistent native review

A reviewer is one dedicated persistent **Qwen Chat/ACP** session. The semantic review and any later native publication continuation must occur in this same conversation.

1. Accept exactly one setup assignment from the orchestrator containing the canonical PR URL, expected SHA, owning worker ID, assigned orchestrator ID, exact `reviewKey`, repair cycle, and selected effort. The selected effort MUST be `high`.
2. Do not edit, format, stage, commit, push, run implementation repair work, merge, label, claim the PR, or perform unrelated GitHub mutation.
3. Do not invoke `qwen review run`, Qwen Monitor, the retired `run_explicit_review.py`, a second Qwen process, or `/ao-pr-review`.
4. Treat the setup assignment as protocol description only, not publication authorization. Mentions of `publish`, `post comments`, or `AO_SEMANTIC_REVIEW_PUBLISH` inside setup/rules describe a future state and MUST NOT satisfy Qwen's user-authorized posting gate. Only a later standalone `AO_SEMANTIC_REVIEW_PUBLISH` control turn from the assigned orchestrator for the exact identity authorizes publication.
5. Record the assigned lifecycle identity in this conversation and acknowledge setup:

```text
ao send --session <ASSIGNED_ORCHESTRATOR_ID> --message 'SEMANTIC_REVIEW_READY
{
  "reviewSessionId": "<AO_SESSION_ID>",
  "reviewKey": "<ASSIGNED_REVIEW_KEY>",
  "prUrl": "<ASSIGNED_CANONICAL_URL>",
  "expectedHead": "<ASSIGNED_SHA>",
  "selectedEffort": "high"
}'
```

Send READY as the final lifecycle action of this setup turn, then end the Chat turn immediately. Do not begin semantic review until the orchestrator starts a separate idle Chat turn containing the native slash command.

### Native review turn

The only valid AO review turn is:

```text
/review <ASSIGNED_CANONICAL_URL> --effort high
```

Any other target or effort is a protocol error: send `SEMANTIC_REVIEW_FAILURE` and do not start review. The native Step 1 parse verdict must also have `comment.effective == false`; if it is true, stop before semantic execution/publication and report `SEMANTIC_REVIEW_FAILURE` because AO publication authorization has not yet occurred.

This is a **two-phase AO-managed review**. The publication decision is intentionally unresolved while the semantic verdict is produced. Execute the native `/review` normally to completion, including its normal persistence and cleanup behavior. Do not post comments/reviews during this verdict phase and do not rerun the review merely to report its result.

After native review completion, send one compact lifecycle envelope using the completed native review's semantic values:

```text
ao send --session <ASSIGNED_ORCHESTRATOR_ID> --message 'SEMANTIC_REVIEW_RESULT
{
  "reviewSessionId": "<AO_SESSION_ID>",
  "reviewKey": "<ASSIGNED_REVIEW_KEY>",
  "prUrl": "<ASSIGNED_CANONICAL_URL>",
  "reviewedHead": "<NATIVE_REVIEWED_HEAD_SHA>",
  "selectedEffort": "<NATIVE_EFFORT>",
  "semanticEvent": "<APPROVE|COMMENT|REQUEST_CHANGES>",
  "baseEvent": "<APPROVE|COMMENT|REQUEST_CHANGES>"
}'
```

Do not send findings through AO. Native artifacts and any native follow-up state remain Qwen-owned; AO does not validate, recompose, or prescribe their internal handling. Send `SEMANTIC_REVIEW_RESULT` as the final lifecycle action of this review turn, then end the Chat turn immediately and remain idle.

If native review is interrupted or cannot produce a terminal semantic event, send one failure message, retain whatever native evidence remains, and do not retry automatically:

```text
ao send --session <ASSIGNED_ORCHESTRATOR_ID> --message 'SEMANTIC_REVIEW_FAILURE
{
  "reviewSessionId": "<AO_SESSION_ID>",
  "reviewKey": "<ASSIGNED_REVIEW_KEY>",
  "prUrl": "<ASSIGNED_CANONICAL_URL>",
  "expectedHead": "<ASSIGNED_SHA>",
  "error": "<EXACT_ERROR_TEXT>"
}'
```

### Publication continuation

Only an `AO_SEMANTIC_REVIEW_PUBLISH` control turn from the assigned orchestrator for the exact assigned `reviewKey` and expected head authorizes publication.

On that message:

1. Verify the control identity against this conversation's assignment. Any mismatch => `SEMANTIC_REVIEW_PUBLICATION_FAILURE`, no GitHub mutation.
2. Continue the **already-completed** native review through Qwen's normal native `post comments` follow-up in this same session. Do not start a fresh `/review` or manually construct a submit payload.
3. Let Qwen's native review machinery own its normal follow-up mechanics, presubmit, convergence, provider downgrade rules, event/body/inline composition, submission, and any internal recovery it normally performs. AO adds no temporary-state rules.
4. AO authorization remains bound to the assigned exact SHA. If the native follow-up determines that the live PR head no longer equals the assigned reviewed SHA, do not publish the old verdict and do not convert this authorization into a review of the new SHA. Send `SEMANTIC_REVIEW_PUBLICATION_FAILURE` with `reasonCode: "head_moved"` and the observed `liveHead`; AO must qualify the new head separately.
5. Send on success:

```text
ao send --session <ASSIGNED_ORCHESTRATOR_ID> --message 'SEMANTIC_REVIEW_PUBLISHED
{
  "reviewSessionId": "<AO_SESSION_ID>",
  "reviewKey": "<ASSIGNED_REVIEW_KEY>",
  "reviewedHead": "<ASSIGNED_SHA>"
}'
```

For any publication failure, send exactly one bounded failure envelope:

```text
ao send --session <ASSIGNED_ORCHESTRATOR_ID> --message 'SEMANTIC_REVIEW_PUBLICATION_FAILURE
{
  "reviewSessionId": "<AO_SESSION_ID>",
  "reviewKey": "<ASSIGNED_REVIEW_KEY>",
  "expectedHead": "<ASSIGNED_SHA>",
  "reasonCode": "<head_moved|identity_mismatch|submit_failed|other>",
  "liveHead": "<40_CHAR_SHA_OR_NULL>",
  "error": "<BOUNDED_ERROR_TEXT>"
}'
```

Any publication error is terminal for the AO lifecycle unless Qwen's native follow-up itself resolves it within the same authorized turn. AO does not send a second publish authorization and does not start a fresh review.

### Discard continuation

Only an `AO_SEMANTIC_REVIEW_DISCARD` control turn from the assigned orchestrator for the exact assigned identity authorizes discard.

Do not post anything. Send:

```text
ao send --session <ASSIGNED_ORCHESTRATOR_ID> --message 'SEMANTIC_REVIEW_DISCARDED
{
  "reviewSessionId": "<AO_SESSION_ID>",
  "reviewKey": "<ASSIGNED_REVIEW_KEY>",
  "expectedHead": "<ASSIGNED_SHA>"
}'
```

After `SEMANTIC_REVIEW_PUBLISHED`, `SEMANTIC_REVIEW_DISCARDED`, or `SEMANTIC_REVIEW_PUBLICATION_FAILURE`, stop. Never route findings, repair, retry, or merge.
