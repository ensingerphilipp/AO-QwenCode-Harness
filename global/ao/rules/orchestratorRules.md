# AO orchestrator rules

These rules apply to AO orchestrators coordinating implementation workers and dedicated semantic-review Tasks. Global Qwen rules and repository-local project contracts remain applicable.

## Boundaries

- Coordinate; do not implement code or perform semantic review in the orchestrator session.
- Do not merge, close issues, change AO configuration, or perform unrelated GitHub mutations.
- Do not launch any competing semantic-review path for the same PR/SHA while this harness lifecycle owns it. One `reviewKey` has exactly one active reviewer lifecycle.
- Resolve repository-root `.agent-harness.json` before PR qualification. Semantic review is enabled by default; only valid schemaVersion 1 config with `semanticReview.enabled` exactly `false` disables it. Malformed/unreadable config requires human attention.
- Start PR qualification only from `READY_FOR_REVIEW` or `READY_FOR_REREVIEW` sent by the owning implementation worker.
- AO owns lifecycle identity, readiness, admission, publication authorization, routing, and `ao/semantic-review` status. Native Qwen `/review` owns semantic reasoning, findings, convergence, GitHub review composition, inline comments, review event, and review cleanup.
- Do not build a second semantic artifact validator, findings compositor, severity/convergence algorithm, or Qwen submit payload in AO.

## Qualify readiness

Before semantic review:

1. Confirm the worker session and PR association.
2. Require an open, non-draft PR and full expected SHA matching the live head.
3. Require `bash scripts/verify: pass` in the worker handoff.
4. Require at least one required deterministic GitHub check and all such checks passing for that exact head. Exclude only exact status context `ao/semantic-review`.
5. Re-read the live PR head immediately before dispatch.
6. Resolve `reviewKey = owner/repo#<PR>@<SHA>` and deduplicate against active/completed reviewer lifecycles.

Pending deterministic checks are not failure. Defer without busy-polling. Route clearly PR-caused CI failure as `CI_FIX_REQUEST`; ambiguous/infrastructure failures require human attention.

If semantic review is disabled, stop after deterministic qualification. Do not create reviewer Tasks or `ao/semantic-review` publication.

## Acquire host-global review admission

Before creating a reviewer, request admission through `~/.local/bin/ao-review-queue` using the exact review identity. The queue is strict host-wide FIFO and belongs to AO/control state.

- Request only after qualification. New queue requests have no resume option; the stored `resumeAttempt` field is retained only for old-state compatibility and is always false for v0.4 tickets. There is no automatic review resume in this version.
- `queued`: retain only ticket identity; create no reviewer/model activity and publish no pending status.
- `granted`: re-read live head and deterministic required checks before reviewer creation.
- A promoted ticket is delivered by one `REVIEW_SLOT_GRANTED ticketId=<ID> reviewKey=<KEY>` message; verify ticket-scoped status before acting.
- Keep the active ticket for the entire AO semantic-review lifecycle: native review execution, AO publish/discard decision, any native publication continuation, and terminal acknowledgement. The verdict alone is not terminal queue state because the same reviewer session is still reserved for the publication decision.
- On terminal publication/discard/failure, release promptly and notify any promoted next orchestrator once.
- A stranded active ticket requires explicit operator recovery; there is no lease TTL or automatic stale-ticket cancellation.

## Review effort

AO-managed reviews MUST run at native `high` effort. Qwen native PR publication is high-only; low/medium reviews cannot later enter the supported `post comments` path. There is therefore no separate harness effort selector or project review-risk configuration.

## Non-posting verdict preflight

Before creating the reviewer, run Qwen's deterministic argument parser for the exact raw argument string `<canonical-PR-URL> --effort high` using `qwen review parse-args --stdin`. Require: target resolves to that canonical PR URL, `effort == high`, `comment.requested == false`, `comment.effective == false`, and no unknown/extra tokens. This is an authorization preflight, not semantic artifact validation.

`comment.effective == true` is a hard incompatibility: operator-scope `review.comment: true` would make the verdict-phase `/review` public before AO authorization. Do not create a reviewer, do not publish pending status, release the queue ticket, and require the operator to disable that standing setting before retrying.

## Dispatch the persistent reviewer

- Spawn exactly one dedicated Qwen reviewer Task labelled `rev-pr-<NUMBER>` in **Chat/ACP mode**. Persistent idle follow-up turns are required by this protocol.
- Omit issue/prompt payloads at spawn; after startup send the complete setup assignment with directed `ao send` using Chat steering.
- Setup assignment MUST contain `AO_SEMANTIC_REVIEW`, orchestrator ID, owning worker ID, canonical PR URL, expected SHA, reviewKey, selected effort (`high`), repair cycle, and the reviewer-mode rules below.
- The setup assignment is descriptive protocol context only. Even though it names future publish/discard controls, it MUST NOT be interpreted as a request to publish this review. Only a later standalone `AO_SEMANTIC_REVIEW_PUBLISH` control turn for the exact identity grants publication authorization.
- Require `SEMANTIC_REVIEW_READY` before starting `/review`. On missing acknowledgement, re-probe once; otherwise fail closed.
- After READY, re-read the live head one final time. If it moved before native review began, terminate the unused reviewer, release the ticket, and wait for a fresh handoff; there is no native review state to publish or discard.
- Start the semantic review by sending a separate Chat turn containing exactly the native slash command:

```text
/review <canonical-PR-URL> --effort high
```

- Do not invoke `/ao-pr-review`, `qwen review run`, Qwen Monitor, the retired Python runner, or a second Qwen process.
- After dispatch, keep the orchestrator available. Do not poll the reviewer or infer completion from runtime duration.

Publish `ao/semantic-review=pending` only after reviewer setup and native `/review` dispatch succeed, under the publication policy.

### Reliable Chat delivery

Every orchestrator -> reviewer turn (setup, native `/review`, publish, discard) MUST start only when AO reports that reviewer Chat session **idle**. `--steer` against a working Chat turn changes that turn instead of starting the next one and can corrupt slash-command/control semantics. After receiving `SEMANTIC_REVIEW_READY` or `SEMANTIC_REVIEW_RESULT`, do not immediately steer: wait until AO session state is idle. A bounded session-state check used only to establish this transport boundary is permitted; do not poll semantic progress or repeatedly prompt the reviewer. A terminated/unrecoverable session fails closed.

Every phase turn MUST use Chat steering with one stable phase-specific client message ID:

```text
ao send --session <REVIEWER_ID> --steer \
  --client-message-id <REVIEW_KEY>:<setup|review|publish|discard> \
  --message '<TURN>'
```

If delivery/result is uncertain, recover that exact steering operation; do not send a semantically equivalent turn under a new ID:

```text
ao send --session <REVIEWER_ID> --steer --recover-only \
  --client-message-id <SAME_ID>
```

This is especially mandatory for publish: an uncertain response must never become a second `post comments` turn. A phase may have exactly one semantic send identity.

## Receive the semantic result

Wait for `SEMANTIC_REVIEW_RESULT` or `SEMANTIC_REVIEW_FAILURE` from the assigned reviewer.

For `SEMANTIC_REVIEW_RESULT` require message identity to match the assigned reviewer session, reviewKey, PR URL, expected/reviewed head, and selected effort. Require `semanticEvent` from `APPROVE`, `COMMENT`, `REQUEST_CHANGES`; require `baseEvent` from the same vocabulary. This is lifecycle-envelope validation only, not semantic artifact validation.

Re-read the live PR head immediately. If it differs from expected/reviewed head, the old result is stale and MUST NOT be published natively.

Map the semantic result narrowly:

- `semanticEvent == REQUEST_CHANGES` -> `blocked`.
- `semanticEvent == APPROVE` -> `pass`.
- `semanticEvent == COMMENT` with `baseEvent == REQUEST_CHANGES` -> `needs_human`.
- other `semanticEvent == COMMENT` -> `pass`.
- identity/session/publication-control failure -> `review_error`.
- moved live head -> `stale`.

Do not inspect/reclassify individual findings. Qwen's native convergence already owns what is published.

There is no automatic timeout/resume path in this architecture. Interruption, reviewer-session loss, or incomplete native review fails closed to human attention. Never spawn a replacement reviewer to publish a prior result. If `SEMANTIC_REVIEW_FAILURE` arrives while the assigned reviewer is still available, send the exact-identity discard control before releasing the queue ticket. If the reviewer is unavailable, release only as a terminal review error.

## Authorize publish or discard

The reviewer MUST remain alive after `SEMANTIC_REVIEW_RESULT` until AO sends exactly one terminal control decision. Native `/review` itself is allowed to complete its normal lifecycle, including cleanup; AO does not prescribe or depend on Qwen's temporary review-state retention.

### Publish

For unchanged exact identity, send the same idle reviewer a structured `AO_SEMANTIC_REVIEW_PUBLISH` turn containing reviewKey and expected head. Immediately before sending it, re-read the live head once more; any mismatch switches to discard/stale instead.

The publish instruction authorizes only the native Qwen `/review` continuation equivalent to **post comments** for the already-completed review. It MUST NOT authorize a fresh review, implementation edits, labels, merge, or unrelated GitHub mutation.

Wait for `SEMANTIC_REVIEW_PUBLISHED` or `SEMANTIC_REVIEW_PUBLICATION_FAILURE`. If publication failure carries `reasonCode=head_moved`, no native review was posted and the lifecycle becomes `stale`, not generic `review_error`.

### Discard

For stale results, human-only/ambiguous failure before publication, or an explicit lifecycle cancellation, send `AO_SEMANTIC_REVIEW_DISCARD` to the same reviewer. It authorizes no GitHub review mutation. Wait for `SEMANTIC_REVIEW_DISCARDED` or failure.

If the reviewer session is unavailable before publish/discard completes, publication is forbidden. Publish `ao/semantic-review=error` only when exact publication identity is independently known and release the queue ticket. Do not recreate the session solely to publish.

## Route the terminal result

Only after publication/discard reaches a terminal acknowledgement:

- `pass`: publish terminal success status and report ready for human integration.
- `blocked`: publish terminal failure status. On repairCycle 0 only, route one `REVIEW_FIX_REQUEST` to the original worker telling it to address the native Qwen review on this exact PR/SHA, provided doing so is in scope and outside project HITL boundaries. AO does not copy/rewrite the findings.
- `needs_human`: publish terminal error status and stop for judgment.
- `stale`: publish stale/error only on the reviewed SHA when identity is trustworthy; never transfer the verdict to the new head.
- `review_error`: publish error when exact identity is trustworthy and stop for human attention.
- A publication failure with `reasonCode=head_moved` is routed as `stale`; never transfer the old verdict to the new head.

Never merge automatically.

## One repair and rereview maximum

- The original worker may perform one repair cycle from the native GitHub review and return `READY_FOR_REREVIEW` with `repairCycle=1`, `previousReviewedSha`, new exact head, and passing local verification.
- Repeat full readiness, FIFO admission, fresh persistent reviewer, native `/review`, authorization, and publication for the new SHA.
- Any non-pass rereview stops for human attention. Never send a second automatic `REVIEW_FIX_REQUEST`.

## Semantic-review publication

Follow `semanticReviewPublication.md`. AO owns only lifecycle authorization and `ao/semantic-review` commit status; native Qwen owns the GitHub review and inline threads. The harness no longer creates or updates an AO semantic-review summary comment.
