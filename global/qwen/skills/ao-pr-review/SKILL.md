---
name: ao-pr-review
description: AO-managed persistent native Qwen review protocol.
user-invocable: false
disable-model-invocation: true
---

# ao-pr-review

This Skill documents the AO-managed semantic-review lifecycle. The semantic engine is Qwen Code's native `/review` executed directly in one persistent dedicated reviewer session.

The old nested execution path (`Qwen Monitor` -> `run_explicit_review.py` -> `qwen review run`) is retired. Do not recreate it. There is no harness semantic artifact validator, result synthesizer, or effort selector.

## Review effort

AO-managed publish-capable reviews always use native `high` effort. Qwen's supported PR publication path is high-only; low/medium reviews cannot later satisfy the native `post comments` continuation. Manual non-posting reviews outside AO may still use native `/review` however the operator chooses.

## AO reviewer protocol

1. AO qualifies PR identity and deterministic CI and obtains the host-global review slot.
2. AO preflights the exact review arguments with native `review parse-args` and requires `comment.effective == false`; a standing operator `review.comment: true` blocks AO review because it would post before AO authorization.
3. AO creates exactly one persistent Qwen Chat/ACP reviewer and assigns effort `high`.
4. Reviewer receives `AO_SEMANTIC_REVIEW` setup and acknowledges `SEMANTIC_REVIEW_READY` without starting review.
5. AO sends one native `/review <canonical-url> --effort high` turn to that same session.
6. Reviewer performs native review without posting, lets the native run complete normally, and reports the semantic event metadata to AO.
7. AO independently rechecks the live head and sends exactly one `AO_SEMANTIC_REVIEW_PUBLISH` or `AO_SEMANTIC_REVIEW_DISCARD` control turn.
8. Publish means continue the existing native review through Qwen's normal `post comments` follow-up in the same session. Discard means no GitHub review mutation.
9. Reviewer sends terminal acknowledgement and stops.

## Ownership

- Native Qwen `/review`: semantic reasoning, findings, verification, convergence, report, publication projection, inline anchors, review event/body, submit and cleanup.
- AO orchestrator: exact identity/readiness, FIFO admission, lifecycle disposition from the semantic event, live-head revalidation, publish/discard authorization, repair routing, `ao/semantic-review` status.
- Implementation worker: scoped code repair only after AO routing, using the published native Qwen review as the finding surface.
- Human: product judgment and merge.

AO does not validate/recompose semantic artifacts and does not maintain a second semantic-review PR summary.

## Interruption policy

There is no automatic timeout/resume in this version. Loss/interruption of the persistent reviewer or incomplete native review fails closed. Do not spawn a replacement reviewer to publish an earlier review.
