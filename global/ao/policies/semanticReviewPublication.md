# AO Semantic-Review Publication Policy

<!-- BEGIN AO_SEMANTIC_REVIEW_PUBLICATION_V1 -->

This policy governs only publication of the harness semantic-review lifecycle to GitHub. It applies when semantic review is enabled for the project.

## Ownership and authorization

- `ao-pr-review` is strictly non-posting and must not mutate GitHub.
- The AO orchestrator alone owns the publications authorized by this policy.
- The only authorized mutations are:
  1. commit status context `ao/semantic-review`; and
  2. one AO-owned pull-request summary comment.
- This policy does not authorize code changes, commits, branches, pull-request reviews or approvals, change requests, inline comments, labels, issue changes, or merges.
- If semantic review is disabled, create or update neither publication.
- GitHub publication never changes the semantic disposition produced by `ao-pr-review`.

## Trust boundary

A terminal semantic verdict may be published only from a persisted result that the orchestrator has validated against the installed `ao-pr-review` contract and the exact dispatched repository, pull request, and expected head SHA.

Immediately before terminal publication, re-read the live pull-request head. Never infer a verdict from Task state, prose, stdout, heartbeats, monitor completion, or process exit alone.

If result validation fails, do not publish a semantic verdict. Follow Failure handling.

## Commit status

Use the exact commit-status context:

`ao/semantic-review`

Use the canonical pull-request URL as the target URL. Before writing a status, read the current status for this context; if state, description, and target URL already match the desired publication, do nothing.

After readiness, exact-head validation, and all required deterministic checks have passed, publish on the exact expected head:

- state: `pending`
- description: `AO semantic review running for <SHORT_SHA>`

For a trustworthy terminal result, publish on the reviewed SHA only:

| Semantic disposition | GitHub state | Description |
|---|---|---|
| `pass` | `success` | `AO semantic review passed for <SHORT_SHA>` |
| `blocked` | `failure` | `AO semantic review found blocking issues for <SHORT_SHA>` |
| `needs_human` | `error` | `AO semantic review needs attention for <SHORT_SHA>` |
| `review_error` | `error` | `AO semantic review failed for <SHORT_SHA>` |
| `stale` | `error` | `AO semantic review stale for <SHORT_SHA>` |

Never transfer a status verdict to a different SHA. If the live PR head moved, only the reviewed SHA may receive the stale terminal state; the new head requires a fresh qualified review.

## Pull-request summary

Maintain exactly one AO-owned PR comment containing this marker:

`<!-- ao-semantic-review-summary:v1 -->`

Resolve an existing marked comment only when it was authored by the currently authenticated AO GitHub identity.

- Exactly one matching AO-authored comment: update it.
- No matching AO-authored comment: create it.
- More than one matching AO-authored comment: make no comment mutation and report `PUBLICATION_ERROR`.
- Never modify another author's comment.
- Never create a replacement merely because the existing marked comment is outdated.

The visible comment must begin with `## AO Semantic Review` and concisely include:

- publication state: `RUNNING`, `PASS`, `BLOCKED`, `NEEDS HUMAN`, `REVIEW ERROR`, or `STALE`;
- PR number and canonical URL;
- reviewed full head SHA and current live head SHA;
- requested and selected effort, with selection reasons when available;
- native and base review events;
- required deterministic CI result, excluding only the exact `ao/semantic-review` context;
- semantic disposition;
- every finding, including informational findings, with stable ID, severity, confidence, summary, and source location;
- `reviewKey` and `attemptId`;
- recommended next action.

A `PASS` summary must preserve non-blocking findings. Pass means no finding blocks the reviewed head; it does not mean that no findings exist.

## Sensitive information

Never publish raw logs, credentials, tokens, environment variables, absolute local filesystem paths, or internal transport diagnostics. It is sufficient to state that local evidence was retained.

## Failure handling

If review transport fails, the Task is cancelled, no trustworthy result exists, or result identity/contract validation fails:

- never publish `success` or `failure` as a semantic verdict;
- if repository, PR, and expected SHA remain independently trustworthy, publish `error` on that expected SHA and update the AO summary to `REVIEW ERROR`;
- if publication identity itself is uncertain, perform no GitHub mutation;
- report semantic-review failure and publication failure separately;
- never fabricate findings or reconstruct a verdict from prose;
- do not retry publication indefinitely.

A publication failure does not invalidate an independently trustworthy local semantic result.

## Lifecycle

For each qualified exact-SHA semantic review:

1. Qualify readiness and deterministic CI under the global orchestrator rules.
2. Dispatch exactly one semantic review for the expected SHA.
3. Publish or retain the idempotent pending status and `RUNNING` summary.
4. Receive the terminal reviewer result or failure without busy-polling.
5. Validate any persisted result against the installed `ao-pr-review` contract and dispatch identity.
6. Re-read the live PR head.
7. Publish the exact-SHA terminal status and update the single AO summary.
8. Route subsequent repair or human action under the global orchestrator rules.

Never merge automatically. Merge remains a human decision.

<!-- END AO_SEMANTIC_REVIEW_PUBLICATION_V1 -->
