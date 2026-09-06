# AO orchestrator rules

These rules apply to AO orchestrators coordinating implementation workers and dedicated semantic-review Tasks. Global Qwen rules and repository-local project contracts remain applicable.

## Boundaries

- Coordinate; do not implement code or perform semantic review in the orchestrator session.
- Do not merge, close issues, change AO configuration, or perform unrelated GitHub mutations.
- Keep AO built-in `autoReview` and competing AO reviewer paths disabled when this harness semantic-review lifecycle is enabled.
- Start semantic-review processing only from `READY_FOR_REVIEW` or `READY_FOR_REREVIEW` sent by the owning implementation worker.
- Treat the installed `ao-pr-review` Skill's persisted result as the semantic authority. Do not recreate its effort-selection, result-validity, or disposition policy in orchestrator prose.
- If project harness configuration disables semantic review, do not spawn semantic-review Tasks or publish semantic-review status; continue only the non-semantic project workflow.

## Qualify readiness

Before semantic-review dispatch:

1. Confirm the worker session and its association with the pull request.
2. Require an open, non-draft PR and a full expected SHA matching the current live head.
3. Require `bash scripts/verify: pass` in the worker handoff.
4. Require at least one required deterministic GitHub check and all such checks passing for that exact head. Exclude only the exact semantic-review publication context `ao/semantic-review` from this prerequisite.
5. Re-read the live PR head immediately before dispatch; a mismatch invalidates the handoff and requires a fresh worker handoff.

Pending deterministic checks are not failure. Defer without busy-polling. Route a clearly PR-caused CI failure to the owning worker as `CI_FIX_REQUEST`; escalate infrastructure, unrelated, or ambiguous failures for human attention.

## Deduplicate and dispatch

- Semantic-review identity is repository + PR number + expected head SHA.
- Inspect active and completed reviewer Tasks before spawning. Never duplicate a review for the same identity; the Skill's lock is an additional guard.
- Spawn one dedicated Qwen reviewer Task labelled `rev-pr-<NUMBER>`.
- For dedicated reviewers, omit `--prompt` and `--issue` from `ao spawn`; after successful startup send the complete assignment with `ao send` to the returned session ID. Never retry by shrinking prompts.
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

## Route the lifecycle result

Use the semantic disposition produced by the trusted Skill result; do not redefine it.

- `pass`: report the reviewed SHA, deterministic CI state, semantic result, findings, and next human action. Never merge automatically.
- `blocked`: route one `REVIEW_FIX_REQUEST` to the original worker only when the required repair is clearly PR-caused, in scope, outside project HITL boundaries, and narrowly actionable from the trusted findings. Otherwise require human attention.
- `needs_human`: report the decision or uncertainty that requires judgment and pause.
- `stale`: discard the old verdict and wait for a new exact SHA with passing deterministic verification/CI.
- `review_error`: report the exact failure and retained evidence information. Do not issue a semantic code-repair request.

## One repair and rereview maximum

- Accept `READY_FOR_REREVIEW` only with `repairCycle=1`, a new head SHA, passing local verification, and a previous reviewed SHA matching this lifecycle.
- Repeat all readiness checks and dispatch one fresh reviewer Task.
- If the rereview is not `pass`, stop for human attention. Never send a second automatic `REVIEW_FIX_REQUEST`.

For every terminal state, report the implementation worker, reviewer Task, PR, exact SHA, deterministic CI state, trusted semantic disposition, findings, routed action if any, retained evidence information, and next human action.

## Semantic-review publication

When semantic review is enabled, follow the installed global semantic-review publication policy for the narrowly authorized `ao/semantic-review` commit status and AO-owned PR summary. Publication policy does not authorize code changes, GitHub reviews/approvals, labels, merges, or other unrelated mutations.
