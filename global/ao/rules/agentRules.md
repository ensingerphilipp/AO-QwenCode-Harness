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
- Reviewer tasks end with `SEMANTIC_REVIEW_RESULT` or `SEMANTIC_REVIEW_FAILURE`.
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
- After sending the final report, stop working and remain available. The orchestrator verifies the reported state and terminates the session.

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
- Act on `REVIEW_FIX_REQUEST` only for findings the orchestrator explicitly routed as automatically repairable. Independently preserve assigned scope and all project HITL boundaries.
- Apply all eligible semantic-review fixes together, run `bash scripts/verify`, push, and send `READY_FOR_REREVIEW`.

```text
ao send --session <ACTIVE_ORCHESTRATOR_ID> --message 'READY_FOR_REREVIEW
{
  "workerSessionId": "<AO_SESSION_ID>",
  "prNumber": <NUMBER>,
  "prUrl": "<CANONICAL_URL>",
  "previousReviewedSha": "<OLD_SHA>",
  "headSha": "<NEW_40_CHARACTER_SHA>",
  "localVerification": "bash scripts/verify: pass",
  "addressedFindingIds": ["<ID>"],
  "repairCycle": 1
}'
```

Never perform a second automatic semantic repair. If a routed request would require scope expansion, a protected project decision, or uncertain judgment, send `NEEDS_HUMAN` to the active orchestrator with `ao send` instead.

## Reviewer mode

1. Accept exactly one review assignment from the orchestrator containing the canonical PR URL, expected SHA, owning worker ID, and orchestrator ID.
2. Outside the review Skill, do not edit, format, stage, commit, push, run project commands, repair findings, post to GitHub, merge, or claim the PR.
3. Execute the installed `ao-pr-review` Skill's **AO reviewer Task entry point** using exactly the assigned canonical PR URL, expected SHA, and `auto` effort request. This AO-dispatched path is not the operator slash-command path. Follow the Skill-owned entry-point procedure exactly; do not substitute `/ao-pr-review`, `/review`, or invoke `qwen review run` yourself, and do not alter or reconstruct the assigned values. The Skill owns helper/Monitor transport, effort selection, result validation, and semantic disposition.

4. Treat monitor heartbeats only as liveness. Do not poll, retry, terminate a quiet review, or impose a shorter timeout than the Skill.
5. After a trusted terminal result, read the exact `result.json` reported by the Skill and send one compact `SEMANTIC_REVIEW_RESULT` to the assigned orchestrator with `ao send`. Copy the result identity and authoritative `resultJson` path without reinterpretation; the orchestrator reads disposition/findings/evidence from that file rather than duplicating them in the message.

```text
ao send --session <ASSIGNED_ORCHESTRATOR_ID> --message 'SEMANTIC_REVIEW_RESULT
{
  "reviewSessionId": "<AO_SESSION_ID>",
  "reviewKey": "<RESULT_VALUE>",
  "attemptId": "<RESULT_VALUE>",
  "resultJson": "<ABSOLUTE_PATH>"
}'
```

If the monitor is cancelled, emits a transport error, or produces no trusted `result.json`, send one failure message:

```text
ao send --session <ASSIGNED_ORCHESTRATOR_ID> --message 'SEMANTIC_REVIEW_FAILURE
{
  "reviewSessionId": "<AO_SESSION_ID>",
  "prUrl": "<ASSIGNED_CANONICAL_URL>",
  "expectedHead": "<ASSIGNED_SHA>",
  "error": "<EXACT_ERROR_TEXT>"
}'
```

Stop after sending one result or failure. Never route findings, repair, retry, or start another review.
