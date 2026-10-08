# ao-pr-review contract (contractVersion=7)

## Scope

This contract defines the AO lifecycle envelope around one persistent native Qwen `/review`. It intentionally does not define or validate Qwen's semantic artifact schema.

## Identity

Stable identity:

```text
reviewKey = owner/repo#<PR-number>@<40-character-head-SHA>
```

The orchestrator independently qualifies an open, non-draft PR, deterministic required CI and exact head before admission and again before review dispatch. Native publication requires another live-head equality check after review and immediately before the publish control turn.

## Review effort

AO-managed review effort is fixed to native `high`. This is a publication requirement, not a risk heuristic: Qwen's supported native PR posting path does not post low/medium reviews. No project risk/effort config participates in the lifecycle.

## Non-posting verdict preflight

Before AO sends the native `/review` turn, it runs Qwen's deterministic `review parse-args --stdin` against the exact raw string `<canonical-PR-URL> --effort high`. Dispatch is allowed only when the parser returns the same PR target, `effort == high`, `comment.requested == false`, `comment.effective == false`, and no unknown/extra tokens. A standing operator `review.comment: true` therefore fails closed before review; project `.qwen/settings.json` cannot enable that Qwen setting, but operator scopes can.

## Persistent reviewer state machine

```text
created
  -> ready
  -> reviewing
  -> verdict_pending_decision
  -> publishing -> published -> terminal
  -> discarding -> discarded -> terminal

Any invalid transition -> review_error / human attention
```

The reviewer is one Qwen Chat/ACP session throughout. The native `/review` turn and later publication/discard turn occur in that same conversation.

## Lifecycle messages

Reviewer setup acknowledgement:

```text
SEMANTIC_REVIEW_READY
reviewSessionId, reviewKey, prUrl, expectedHead, selectedEffort
```

Verdict handoff:

```text
SEMANTIC_REVIEW_RESULT
reviewSessionId, reviewKey, prUrl, reviewedHead, selectedEffort,
semanticEvent, baseEvent
```

Allowed semantic/base events are `APPROVE`, `COMMENT`, `REQUEST_CHANGES`. AO validates only this lifecycle envelope and exact identity; it does not validate Qwen semantic artifacts.

Failure:

```text
SEMANTIC_REVIEW_FAILURE
reviewSessionId, reviewKey, prUrl, expectedHead, error
```

AO terminal controls:

```text
AO_SEMANTIC_REVIEW_PUBLISH reviewKey expectedHead
AO_SEMANTIC_REVIEW_DISCARD reviewKey expectedHead
```

Terminal acknowledgements:

```text
SEMANTIC_REVIEW_PUBLISHED reviewSessionId reviewKey reviewedHead
SEMANTIC_REVIEW_DISCARDED reviewSessionId reviewKey expectedHead
SEMANTIC_REVIEW_PUBLICATION_FAILURE reviewSessionId reviewKey expectedHead reasonCode liveHead error
```

## Native run lifecycle

The initial native `/review` is allowed to complete normally, including Qwen's own persistence and cleanup behavior. The AO contract depends on persistence of the **reviewer conversation**, not on any particular `.qwen/tmp` artifact surviving between verdict and publication authorization.

The host-global FIFO ticket remains held until AO receives a terminal publish/discard/failure acknowledgement.

## Native publication

`AO_SEMANTIC_REVIEW_PUBLISH` authorizes only continuation of the already-completed native review through Qwen's native `post comments` path. Qwen, not AO, owns any internal follow-up state handling, payload construction, anchor resolution, presubmit, convergence, event/body, inline publication and cleanup.

AO never constructs or validates the native submit payload. Native presubmit head drift is a harness stop condition: the reviewer must neither submit the old SHA nor follow native review's ordinary drift-restart path to a new SHA under the old authorization. It returns publication failure with `reasonCode=head_moved`; Qwen owns internal state handling, and only a newly qualified AO lifecycle may review the new head.

`AO_SEMANTIC_REVIEW_DISCARD` authorizes no GitHub review mutation.

## Lifecycle disposition

AO maps only the pre-publication semantic event plus lifecycle identity:

```text
semanticEvent REQUEST_CHANGES -> blocked
semanticEvent APPROVE -> pass
semanticEvent COMMENT + baseEvent REQUEST_CHANGES -> needs_human
other semanticEvent COMMENT -> pass
head moved -> stale
session/control/publication failure -> review_error
```

There is no finding-level AO reclassification.

## Interruption

No automatic resume is defined. Reviewer/session loss or incomplete native `/review` fails closed. A replacement reviewer must not publish a prior review.
