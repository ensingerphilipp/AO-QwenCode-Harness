# ao-pr-review — v0.4.0

`ao-pr-review` documents the AO-managed persistent native review lifecycle. It contains no semantic runner or artifact validator.

## Architecture

A dedicated Qwen **Chat/ACP** reviewer runs native `/review` directly. There is no nested Qwen review process, Monitor envelope, synthetic `result.json`, semantic artifact validator, effort selector, or AO findings compositor.

```text
AO -> persistent reviewer -> native /review --effort high
                         -> native verdict
AO <- SEMANTIC_REVIEW_RESULT
AO -> publish/discard decision
                         -> native post comments OR no post
AO <- terminal acknowledgement
```

Qwen owns semantic review and GitHub review composition. AO owns exact-SHA qualification, admission, publication authorization, routing and the `ao/semantic-review` status. Humans own merge.

## High-only AO review

AO-managed reviews always use native `high` effort because Qwen's supported PR posting path is high-only. The previous deterministic risk/effort selector and `.qwen/review-config.json` project risk metadata are removed rather than retained as ineffective configuration.

The initial verdict phase must also be non-posting. AO preflights the exact arguments with native `qwen review parse-args --stdin` and requires `comment.effective == false`. Operator-scope `review.comment: true` is incompatible with the harness because Qwen treats it like `--comment` and would publish during the initial review.

## Two-phase publication

The verdict phase is deliberately non-posting. Native `/review` is allowed to complete normally, including its own persistence and cleanup. The reviewer then reports lifecycle/semantic event metadata to AO and remains available in the same conversation. AO rechecks the live head and sends one terminal publish/discard decision to that same idle reviewer.

Publish follows Qwen's normal native `post comments` continuation; Qwen owns its internal state handling, presubmit, convergence, event/body/inlines and submit. Discard posts nothing. The FIFO review slot remains held until this second AO phase terminates.

## Removed legacy machinery

v0.4.0 removes the production use of:

- `run_explicit_review.py`;
- `qwen review run`;
- Qwen Monitor review envelopes and heartbeat transport;
- wrapper/companion semantic validation and synthetic `result.json`;
- helper-owned timeouts/resume;
- deterministic risk/effort selection and `.qwen/review-config.json`;
- AO-composed semantic summary comments.

Existing historical AO summary comments and existing project `.qwen/review-config.json` files are left untouched as legacy repository history/config; new harness templates no longer create or consume that file.

## Failure behavior

Session loss, interrupted native review, identity mismatch, head movement, or publication failure fail closed. There is no automatic resume or replacement reviewer publication path. The harness never constructs Qwen's submit payload.
