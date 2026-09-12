---
name: ao-pr-review
description: Run one explicit, non-posting native Qwen semantic review of an exact GitHub PR head after required CI passes, selecting medium or high effort deterministically.
---

# ao-pr-review

Run one explicit, non-posting native Qwen semantic review of an exact GitHub
PR head after required CI passes.

This skill is **non-posting and makes no intended tracked application
changes**: it never posts GitHub reviews or comments, never edits application
source, never repairs findings, and never merges. The native Qwen process may
leave untracked local artifact files behind; the helper records that in the
evidence and leaves everything in place.

## Invocation

This skill has exactly two supported invocation paths:

1. **Operator**: the operator types the slash command in their own Qwen
   session.
2. **Dispatched AO reviewer**: a model acting in a one-shot AO reviewer
   session dispatched by the orchestrator for this review invokes this
   skill with exactly the dispatched arguments — a PR number or canonical
   PR URL, the expected 40-character head SHA, and the effort request
   (`auto`, `medium`, or `high`).

A model that was not dispatched for this review does not invoke this
skill on its own initiative.

Supported forms:

```text
/ao-pr-review <PR-number-or-URL> <EXPECTED-40-CHAR-HEAD-SHA> [auto|medium|high]
/ao-pr-review help
```

- The expected head SHA must be exactly 40 lowercase hexadecimal characters.
- The default effort request is `auto`; the helper deterministically selects
  `medium` or `high` (policy in `references/policy.md`).

Fail-closed argument handling (both paths):

- Arguments pass to the helper **only** via the CLI-injected
  `<skill-args-file>` path. Never retype, reconstruct, or "correct" the
  PR, SHA, or effort from conversational text, examples, or memory.
- The helper runs **only** through Qwen's native `monitor` tool (see
  "Review execution (Qwen Monitor)").
- If a non-help invocation carries no `<skill-args-file>` tag, report that
  the invocation carried no argument file and stop — never retype,
  reconstruct, or guess arguments.

## Mandatory argument handling (Qwen Code 0.22.3)

Qwen Code 0.22.3 writes slash-command arguments verbatim to a session-private
file and injects its path into the user message as:

```text
<skill-args-file>...</skill-args-file>
```

Pass **only that injected file path** to the helper. Do not retype,
reconstruct, or "correct" the PR, SHA, or effort from conversational text,
examples, or memory. Never invent arguments.

The review is launched through Qwen's native `monitor` tool (see
"Review execution (Qwen Monitor)"). The helper command the monitor runs is:

```text
python3 <skill-dir>/scripts/run_explicit_review.py --monitor-envelope --args-file <injected-path>
```

where `<skill-dir>` is the base directory of this skill (the directory
containing this SKILL.md).

If the invocation is `/ao-pr-review help` and the message carries an
args-file, the same command works (the helper recognizes `help`). If the
operator asked for help and no args-file is present, run:

```text
python3 <skill-dir>/scripts/run_explicit_review.py help
```

(`help` is not a real review; it prints usage immediately.)

If a non-help invocation does not carry a `<skill-args-file>` tag, do not
guess arguments: report that the invocation did not carry an argument file
and stop.

## Review execution (Qwen Monitor)

Every real review launches the helper ONLY through Qwen's native `monitor`
tool. Do not use `run_shell_command`: a foreground shell is capped at
600000 ms, which a real review exceeds, and a background shell
settles silently in Qwen Code 0.22.3 — it is observable through `/tasks`
and its output file, but it does not push a completion notification
that resumes the agent, so a reviewer Task can launch a review and go
idle without ever processing the result. The `monitor` tool is the
supported mechanism for streaming task notifications back to the session
that owns the review.

Call the `monitor` tool with exactly:

```text
command:
  python3 <skill-dir>/scripts/run_explicit_review.py --monitor-envelope --args-file <injected-path>

directory:
  the current repository worktree

idle_timeout_ms:
  600000

max_events:
  128
```

Do not add `&`, `nohup`, a foreground shell call, a second watcher,
a polling loop, or a scheduler. There is no shell timeout parameter;
liveness is handled inside the helper by a fixed 480-second transport
`keepalive` event that stays below Qwen 0.23.0's hard 600000 ms monitor idle
timeout while a long review runs.

The monitor-envelope transport streams only bounded, deterministic protocol
events (single-line JSON prefixed with `AO_PR_REVIEW_EVENT=`): a fixed
480-second transport-only `keepalive`, a fixed 960-second `heartbeat` carrying
optional bounded observational progress (`stage`, agent started/completed
counts, and last activity time; never findings or prose), and exactly one
terminal event — `complete` when a trustworthy `result.json` was
persisted and revalidated for this exact invocation, or `transport_error`
otherwise. The helper process exits 0 only for a trustworthy result (any
disposition) and exits nonzero for malformed usage, crash, cancellation,
missing result, persistence failure, or identity mismatch. Neither a
`complete` event nor a Monitor `completed` status means PASS.

After the `monitor` tool starts, report exactly:

- `REVIEW STARTED`
- the monitor ID Qwen returned
- that the dedicated AO reviewer session remains available

Do not claim the PR or the expected head SHA at launch unless validated
metadata already provides them, and do not read, reconstruct, or invent slash
arguments merely for the receipt.

Then end the model turn. Do not wait and do not start any second watcher or
scheduler: the monitor's events are how the result comes back.

A monitor review is a child of its Qwen session.
Terminating the Qwen session can terminate the in-flight review.
An interrupted, cancelled, or auto-stopped review has no valid verdict and
must later be re-run fresh against the exact current head SHA (re-read the
head; never reuse the old SHA).

## Handling Monitor events

When a monitor notification for this review's monitor ID arrives:

- `keepalive` — nonterminal transport liveness only. Do not report progress or
  take any action; stop.
- `heartbeat` — nonterminal. Report `REVIEW RUNNING` with the elapsed time.
  When the event includes validated bounded progress fields, also report the
  stage and `agentsCompleted/agentsStarted`. Do not read the raw Qwen transcript
  yourself, infer findings, or take any action from progress. Then stop.
- `complete` — read and validate the exact `resultJson` path from the event
  (re-read it from disk and check its `reviewKey`, `attemptId`, `disposition`,
  and `semanticExitCode`), then display the full semantic result from
  "Interpreting the result".
- `transport_error`, a failed or cancelled monitor, a missing terminal event,
  or an invalid or missing `result.json` — `REVIEW ERROR`.

Never equate the monitor's `completed` status with a semantic PASS: a
`complete` event (or a `completed` monitor) only means the review operation
produced a trustworthy result; only the validated `disposition` says what it
is. Then stop. Never repair, route, post, retry, schedule, or merge.

Ignore unrelated monitor notifications from other sessions or tasks.

## Interpreting the result

Read the `result.json` at the `complete` event's `resultJson` path (validate
it first) and display to the operator:

1. The disposition as one of: `PASS`, `BLOCKED`, `NEEDS HUMAN`, `STALE`,
   `REVIEW ERROR`.
2. PR number and canonical PR URL.
3. Expected head, before-review head, and after-review head (three distinct
   values; do not collapse them).
4. Requested and selected effort, plus the effort-selection reasons.
5. Native event and base event.
6. Every finding: ID, severity, confidence, summary, failure scenario, and
   outcome when present.
7. The evidence directory (the directory containing `result.json`).
8. The recommended human action (`nextAction`).

Then stop. Never repair, route, post, retry, schedule, or merge.

- `BLOCKED`: identify the findings suitable for sending to the existing
  implementation worker. Do not send them.
- `NEEDS HUMAN`: identify the uncertain premise that requires judgment.
- `STALE`: state that the old verdict is invalidated and show both the
  expected and the observed after-review head SHAs.
- `REVIEW ERROR`: show the deterministic failure (`error`) and the evidence
  location. If no trustworthy `result.json` was persisted, report the helper's
  `transport_error` event instead.

For the low-level `direct` and `background-envelope` transports (manual
transport selection only, not a real `/ao-pr-review` invocation), the helper's
final stdout instead ends with `DISPOSITION=`, `SEMANTIC_EXIT_CODE=`, and
`RESULT_JSON=` lines, and the background-envelope result must be retrieved
explicitly from the task's output file.

## AO reviewer Task entry point

The AO orchestrator creates one dedicated AO reviewer Task for each qualified
exact-SHA review. That Task uses Qwen's native `monitor` tool to run the
positional monitor-envelope command, receives notifications in its own Qwen
session, validates the final result, and reports it to the orchestrator, leaving the main orchestrator unblocked and
available. A dedicated reviewer Task explicitly created by the orchestrator may invoke the positional monitor-envelope form:

```text
python3 <skill-dir>/scripts/run_explicit_review.py \
  --monitor-envelope <PR-or-URL> <EXPECTED-SHA> [auto|medium|high]
```

The Task:

1. launches the helper through Qwen's native `monitor` tool in the current
   repository worktree (`idle_timeout_ms: 600000`, `max_events: 128`);
2. retains its AO session/task identity while the review runs;
3. yields the turn — no waiting, no scheduler, no polling;
4. receives the monitor events (`heartbeat`, then `complete` or
   `transport_error`) in its own Qwen session; and
5. validates and reports `result.json` to the orchestrator.

Explicit assignment by the orchestrator is authorization; it is
not spontaneous Skill invocation. The skill and helper must never:
create AO tasks, send AO messages, change AO configuration or rules,
route findings, repair code, retry automatically, post to GitHub,
merge, schedule or poll. Those lifecycle decisions belong to the installed
AO `agentRules` and `orchestratorRules`. The `reviewKey` recorded in every
result is the stable identity used for deduplication.

## Ownership

- Qwen's native `monitor` tool streams bounded protocol events back to the
  session that launched the review, so a long review (up to eight hours for
  high effort) keeps notifying without the 600000 ms foreground-shell cap and
  without a background shell that settles silently.
- A background shell is observable through `/tasks` and its output file but
  does not push a completion notification; the monitor transport is the
  supported notification mechanism, and its result is retrieved explicitly
  from the validated `result.json`.
- Qwen 0.23.0 hard-caps monitor `idle_timeout_ms` at 600000, so a fixed
  480-second transport-only `keepalive` prevents idle termination. It carries
  no semantic progress and is not a review controller.
- Rich observational `heartbeat` events are emitted every 960 seconds. The
  combined keepalive/heartbeat event count over the eight-hour high-effort
  budget remains well below `max_events: 128`.
- Terminating the Qwen session can terminate its in-flight monitor review.
- An interrupted or cancelled review has no valid verdict and must later be
  re-run fresh against the exact current head SHA.
- For manual qualification, invoke this skill inside a dedicated AO reviewer Task session — not the main orchestrator and not the implementation worker.
- The AO orchestrator rules create and supervise the dedicated reviewer Task;
  the Skill does not own AO scheduling or routing.
- The internal native Qwen review agents remain Qwen implementation
  details.
- The Skill implements semantic-review execution only; AO automation and routing remain outside the Skill.
The Skill has no AO automation or routing. Global `orchestratorRules` owns reviewer-Task creation, coordination, lifecycle routing, and publication authorization.
