# AO orchestrator rules

These rules apply to AO orchestrators coordinating implementation workers and dedicated semantic-review Tasks. Global Qwen rules and repository-local project contracts remain applicable.

## Boundaries

- Coordinate; do not implement code or perform semantic review in the orchestrator session.
- Do not merge, close issues, change AO configuration, or perform unrelated GitHub mutations.
- Keep AO built-in `autoReview` and competing AO reviewer paths disabled when this harness semantic-review lifecycle is enabled.
- Resolve the project lifecycle toggle from repository-root `.agent-harness.json` before PR qualification. Semantic review is enabled by default; only a valid schemaVersion 1 config with `semanticReview.enabled` exactly `false` disables it. A malformed/unreadable config is a configuration error requiring human attention, never an implicit disable.
- Start PR qualification only from `READY_FOR_REVIEW` or `READY_FOR_REREVIEW` sent by the owning implementation worker.
- Treat the installed `ao-pr-review` Skill's persisted result as the semantic authority. Do not recreate its effort-selection, result-validity, or disposition policy in orchestrator prose.
- If project harness configuration disables semantic review, still qualify the exact PR/SHA and deterministic CI, but do not spawn semantic-review Tasks or create/update semantic-review publication. Report the deterministically qualified head as ready for the next human-controlled integration step.

## Qualify readiness

Before completing any PR handoff or dispatching semantic review:

1. Confirm the worker session and its association with the pull request.
2. Require an open, non-draft PR and a full expected SHA matching the current live head.
3. Require `bash scripts/verify: pass` in the worker handoff.
4. Require at least one required deterministic GitHub check and all such checks passing for that exact head. Exclude only the exact semantic-review publication context `ao/semantic-review` from this prerequisite.
5. Re-read the live PR head immediately before dispatch; a mismatch invalidates the handoff and requires a fresh worker handoff.
6. Resolve semantic-review identity as repository + PR number + expected head SHA and inspect active/completed reviewer Tasks. If that exact identity already has a reviewer lifecycle, do not request queue admission or create another reviewer; reconcile the existing lifecycle instead.

Pending deterministic checks are not failure. Defer without busy-polling. Route a clearly PR-caused CI failure to the owning worker as `CI_FIX_REQUEST`; escalate infrastructure, unrelated, or ambiguous failures for human attention.

If semantic review is disabled and all readiness checks pass, stop the automated review lifecycle here. Report the exact qualified head, passing deterministic CI state, and that semantic review was disabled. Never publish `ao/semantic-review` for that lifecycle.

## Acquire host-global review admission

Semantic review is a host-constrained resource. Before creating any reviewer Task, request deterministic admission through the installed `~/.local/bin/ao-review-queue`. Queue identity is the exact `reviewKey` (`owner/repo#<PR>@<SHA>`), and queue state belongs to the orchestrator/control layer, never the worker or reviewer.

- Request admission only after the exact-SHA readiness checks above pass. Supply only machine identity: review key, repository, canonical PR URL/number, expected head, this orchestrator session ID, owning worker session ID, repair cycle, and whether this is the one timeout-resume attempt.
- `granted`: record the returned `ticketId`, immediately re-read the live PR head and deterministic required checks, and only then continue to reviewer creation. A grant is resource admission, not review authorization.
- `queued`: record only `ticketId` + `reviewKey` and stop work on that review. Do not create a reviewer Task, start Monitor, publish semantic-review `pending`, poll queue state, send recurring status messages, or ask a model to retain/reason about the queued review. Queued state is inert deterministic machine state.
- The queue is strict host-wide FIFO across projects. Duplicate requests for the same review key by the same orchestrator are idempotent; conflicting ownership or malformed queue state requires human attention.
- A promoted ticket is delivered by one minimal `REVIEW_SLOT_GRANTED ticketId=<ID> reviewKey=<KEY>` message from the orchestrator that released the prior active ticket. On receipt, verify with ticket-scoped `ao-review-queue status <ticketId>` that the ticket is the active ticket owned by this orchestrator before doing anything else.
- After promotion, repeat the exact-SHA/open/non-draft/deterministic-CI checks before reviewer creation. If requalification fails, release the active ticket without creating a reviewer, notify the next promoted orchestrator if one is returned, and handle the stale/configuration/CI state under the normal lifecycle rules.
- When the native review attempt reaches a terminal result/failure/cancellation, release its active ticket promptly. If `release` returns a next ticket, send exactly one minimal `REVIEW_SLOT_GRANTED` message to that ticket's recorded orchestrator session. The queue helper never sends AO messages itself.
- If an active ticket is stranded because its owning orchestrator/reviewer died, fail closed. There is no lease TTL, session probing, daemon, or automatic stale-ticket recovery; an operator must inspect `ao-review-queue status` and explicitly `cancel` the proven-dead ticket. If cancellation promotes a next ticket, the operator/orchestrator must deliver the same minimal grant message.

## Dispatch a granted review

- Enter this section only while holding the active queue ticket for this exact review identity. Perform one final active/completed reviewer-Task deduplication check before spawning. If the identity is already represented, release the ticket, notify any promoted next orchestrator, and reconcile the existing lifecycle instead; the Skill's per-PR lock remains an additional guard.
- Spawn one dedicated Qwen reviewer Task labelled `rev-pr-<NUMBER>`.
- For dedicated reviewers, omit `--prompt` and `--issue` from `ao spawn`; after successful startup send the complete assignment with `ao send` to the returned session ID. Never retry by shrinking prompts. If reviewer creation or assignment delivery fails, release the active queue ticket, notify any promoted next orchestrator, and handle the dispatch failure without publishing a running semantic-review state.
- The assignment must include `AO_SEMANTIC_REVIEW`, this orchestrator ID, the owning worker ID, canonical PR URL, expected SHA, and reviewer-mode prohibitions.
- Send the reviewer this exact command:

```text
/ao-pr-review <CANONICAL_PR_URL> <EXPECTED_40_CHARACTER_SHA> auto
```

The Skill alone chooses review effort. The reviewer Task owns Qwen Monitor while the review runs; keep the orchestrator available. Do not poll, infer failure from runtime, impose a shorter timeout, or treat heartbeats as results.

Wait for one `SEMANTIC_REVIEW_RESULT` or `SEMANTIC_REVIEW_FAILURE` message.

## Validate the returned result

For `SEMANTIC_REVIEW_RESULT`:

1. Read the exact `resultJson` path.
2. Validate the result according to the installed `ao-pr-review` contract and require its identity to match this dispatch and message.
3. Require the monitor transport expected by the installed Skill for AO review execution.
4. Re-read the live PR head before acting. If it differs from the reviewed expected head, treat the lifecycle result as stale and discard the old verdict.

A missing, malformed, identity-mismatched, cancelled, or transport-failed result is a review error, never a pass. Do not silently retry.

A trusted `review_error` with `timedOut: true` is the sole automatic-resume case. Release the active review-admission ticket first. If the PR head is unchanged and this review identity has not yet been resumed, request a new queue ticket for the same review identity with `resumeAttempt=true`; strict FIFO applies, so this resume joins the back of the host-wide queue. Keep the original reviewer Task/worktree available but idle while queued: do not start Monitor, poll, or send recurring model messages. When the resume ticket is granted and requalified, send that same reviewer Task the same `/ao-pr-review` command so Qwen can attempt native continuation from surviving state. `--resume` is a request, not proof of continuation; do not claim resume succeeded unless Qwen explicitly reports it. Never spawn a replacement reviewer for this continuation. Allow at most one automatic resume per repository + PR + head SHA. If that resume times out/fails, the head changed, or the original reviewer/worktree is unavailable, stop for human attention.

## Route the lifecycle result

Use the semantic disposition produced by the trusted Skill result; do not redefine it.

- `pass`: report the reviewed SHA, deterministic CI state, semantic result, findings, and next human action. Never merge automatically.
- `blocked`: route one `REVIEW_FIX_REQUEST` to the original worker only when the required repair is clearly PR-caused, in scope, outside project HITL boundaries, and narrowly actionable from the trusted findings. Otherwise require human attention.
- `needs_human`: report the decision or uncertainty that requires judgment and pause.
- `stale`: discard the old verdict and wait for a new exact SHA with passing deterministic verification/CI.
- `review_error`: apply the single timeout-resume exception above; otherwise report the exact failure and retained evidence information. Do not issue a semantic code-repair request.

## One repair and rereview maximum

- Accept `READY_FOR_REREVIEW` only with `repairCycle=1`, a new head SHA, passing local verification, and a previous reviewed SHA matching this lifecycle.
- Repeat all readiness checks, obtain a fresh host-global admission ticket, and dispatch one fresh reviewer Task only after that ticket is granted and requalified.
- If the rereview is not `pass`, stop for human attention. Never send a second automatic `REVIEW_FIX_REQUEST`.

For every terminal state, report the implementation worker, reviewer Task, PR, exact SHA, deterministic CI state, trusted semantic disposition, findings, routed action if any, retained evidence information, and next human action.

## Semantic-review publication

When semantic review is enabled, follow the installed global semantic-review publication policy for the narrowly authorized `ao/semantic-review` commit status and AO-owned PR summary. Publication policy does not authorize code changes, GitHub reviews/approvals, labels, merges, or other unrelated mutations.
