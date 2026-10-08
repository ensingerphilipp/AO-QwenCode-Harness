# AO Semantic-Review Publication Policy

<!-- BEGIN AO_SEMANTIC_REVIEW_PUBLICATION_V2 -->

This policy governs publication of the harness semantic-review lifecycle when semantic review is enabled.

## Ownership

- AO owns authorization and the exact commit-status context `ao/semantic-review`.
- The dedicated persistent Qwen reviewer owns native GitHub review publication after explicit AO authorization: event, body, inline comments, thread/convergence behavior, native submit and cleanup.
- AO MUST NOT create a second findings summary, inline-comment projection or submit payload.
- The old AO-owned `<!-- ao-semantic-review-summary:v1 -->` PR comment is retired. Existing historical comments are left untouched; the normal lifecycle creates or updates none.
- The human-only `ao-semantic-review-override` remains limited to setting `ao/semantic-review=success` after its independent preconditions. It does not create or alter a native Qwen review.
- Neither AO nor Qwen may auto-merge.

## Exact-head authorization boundary

Native publication is authorized only after:

1. qualified deterministic CI for the assigned expected SHA;
2. a deterministic `qwen review parse-args --stdin` preflight for `<canonical-PR-URL> --effort high` proves the same PR target, `effort == high`, `comment.requested == false`, `comment.effective == false`, and no unknown/extra tokens; operator-scope `review.comment: true` is incompatible because it would authorize publication during the verdict phase;
3. one completed native `/review` in the assigned persistent reviewer session;
4. a lifecycle result whose reviewer/session/reviewKey/PR/head/effort identity matches the dispatch; and
5. an immediate live PR-head re-read equal to the reviewed expected SHA.

AO then sends `AO_SEMANTIC_REVIEW_PUBLISH` to that same reviewer session. A grant/admission ticket, reviewer assignment, `/review` execution, semantic verdict, or GitHub credential alone is not publication authorization.

The semantic lifecycle result remains the pre-publication Qwen verdict. Provider-side/native submission may neutralize the GitHub review event (for example, GitHub self-authored PRs cannot accept APPROVE/REQUEST_CHANGES and Qwen posts COMMENT instead). That API event change does not rewrite AO's semantic disposition or `ao/semantic-review` status.

If the live head moved, AO sends `AO_SEMANTIC_REVIEW_DISCARD`; the old semantic result is stale and MUST NOT be posted natively.
The same exact-head boundary remains in force inside native publication. If Qwen presubmit detects head drift after AO authorization, the reviewer must abort before submit, must not use Qwen's normal drift-restart behavior to review the new SHA under the old authorization, and report `reasonCode=head_moved`. AO treats that as stale. Qwen owns any internal state handling.

## Commit status

Use exact context `ao/semantic-review` and canonical PR URL as target URL. Read current context first and avoid an identical rewrite.

After reviewer setup and native `/review` dispatch succeed:

- `pending`: `AO semantic review running for <SHORT_SHA>`

After terminal native publication/discard/failure:

| Lifecycle result | status | description |
|---|---|---|
| pass | success | `AO semantic review passed for <SHORT_SHA>` |
| blocked | failure | `AO semantic review found blocking issues for <SHORT_SHA>` |
| needs_human | error | `AO semantic review needs attention for <SHORT_SHA>` |
| stale | error | `AO semantic review stale for <SHORT_SHA>` |
| review_error / publication failure other than head drift | error | `AO semantic review failed for <SHORT_SHA>` |

Never transfer status to another SHA. A new PR head requires a fresh qualified lifecycle.

## Two-phase native publication

The verdict and publication decision are separate AO phases, but Qwen owns the internal lifecycle of each native review turn. The initial `/review` may complete its normal persistence and cleanup behavior. After AO revalidates identity/head, the same reviewer session either:

- receives publish authorization and follows Qwen's normal native `post comments` continuation; or
- receives discard and performs no GitHub review mutation.

The host-global review-admission ticket remains active through this entire AO lifecycle and is released only after publication/discard/failure reaches a terminal acknowledgement.

## Failure handling

- Missing/mismatched reviewer identity, interrupted native review, lost reviewer session, publish failure or uncertain control state never becomes PASS.
- Do not spawn a replacement reviewer merely to publish a prior review and do not construct Qwen's submit payload in AO.
- If the reviewer is lost after verdict but before the AO publication decision completes, publication is forbidden; do not silently recover it with a new semantic run.
- Do not retry native publication indefinitely. One control decision is sent for one review lifecycle.
- When exact repository/PR/SHA identity remains trustworthy, publish `ao/semantic-review=error`; if publication identity itself is uncertain, perform no GitHub mutation.

## Lifecycle

1. Qualify exact SHA and deterministic CI.
2. Acquire host-global FIFO admission.
3. Fix review effort to native `high` and run the deterministic non-posting argument preflight; fail closed before reviewer creation if `comment.effective != false` or the parsed target/input differs.
4. Create one persistent Chat/ACP reviewer and establish exact lifecycle identity.
5. Dispatch one native `/review` turn and publish pending status.
6. Reviewer returns semantic event metadata and remains available in the same conversation.
7. AO revalidates lifecycle identity and live head.
8. AO sends exactly one publish or discard control turn to the same reviewer.
9. Reviewer performs native publication follow-up or discard handling and acknowledges terminal state.
10. AO publishes terminal `ao/semantic-review`, routes at most one repair, releases the queue ticket, and leaves merge to a human.

<!-- END AO_SEMANTIC_REVIEW_PUBLICATION_V2 -->
