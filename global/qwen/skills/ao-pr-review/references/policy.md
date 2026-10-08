# ao-pr-review policy

## Review effort

AO-managed publish-capable semantic reviews always use native `high` effort. Qwen's native PR posting path is high-only, so there is no separate harness risk-to-effort policy and no `.qwen/review-config.json` input. The initial verdict phase additionally requires native parse preflight with `comment.effective == false`; standing operator `review.comment: true` is incompatible with AO-managed review.

## Lifecycle disposition

AO deliberately does not reinterpret Qwen findings. After exact identity/head validation:

1. semantic `REQUEST_CHANGES` -> `blocked`;
2. semantic `APPROVE` -> `pass`;
3. semantic `COMMENT` whose `baseEvent` is `REQUEST_CHANGES` -> `needs_human`;
4. other semantic `COMMENT` -> `pass`;
5. moved head -> `stale`;
6. session/control/publication failure -> `review_error`.

The semantic event is the verdict from the completed native review before publication. Provider-side submission may neutralize the GitHub API event (for example on self-authored PRs); that does not change AO disposition. Publication/discard must complete before the queue ticket and lifecycle are terminal.
