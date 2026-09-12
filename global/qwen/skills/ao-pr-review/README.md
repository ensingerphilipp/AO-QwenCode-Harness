# ao-pr-review — v0.3.3

A host-global Qwen Code Skill that runs **one explicit, non-posting native
Qwen semantic review** of an exact GitHub PR head after required
deterministic CI passes (required checks excluding only the exact context
`ao/semantic-review`).

- `VERSION=0.3.3`
- `contractVersion=6` (result/finding contract)
- `effortPolicyVersion=2` (deterministic medium/high selection with validated project extensions)

## What it does

Given a PR (number or URL), an expected 40-character head SHA, and an effort
request (`auto` by default, or `medium`/`high`):

1. Resolves the PR with read-only `gh` commands and verifies the canonical
   PR URL matches the current repository (fork PRs stay attributed to the
   base repository).
2. Requires the PR to be open, non-draft, at the exact expected head, and
   with all required deterministic checks in `bucket == "pass"` — required
   checks excluding only the exact context `ao/semantic-review` — with at
   least one such check (the excluded context never gates the review;
   missing or malformed checks output fails closed).
3. Selects `medium` or `high` effort deterministically (no model call),
   consuming the real `gh` flattened `files`/`labels` arrays and failing
   closed (high) on missing, wrong-type, or malformed risk metadata.
4. Requires the repository to have no tracked/staged changes before
   launching Qwen, snapshots the local repository identity (tracked/staged
   status, `HEAD`, branch/ref) while holding the lock, re-reads the head as
   the final pre-review check after all preparation (the last operation
   before the single review launch), and re-reads the head and the local
   identity again immediately after (three distinct recorded heads; any
   tracked/staged change, local `HEAD` change — including a child process
   that commits and leaves a clean worktree — or branch/ref change is
   `review_error`, never repaired).
5. Runs exactly one native review:

   ```text
   qwen review run <canonical-PR-URL> \
     --effort <medium|high> --json --fail-on request-changes \
     --approval-mode yolo --timeout-minutes <240|480>
   ```

   `--timeout-minutes` is the deterministic effort-based budget (v0.2.3):
   240 for selected `medium`, 480 for selected `high`. The Python wrapper
   timeout is that budget plus 600 seconds of cleanup grace. A native or
   wrapper timeout remains `review_error`. (`--comment` and `--resume` are
   never passed.)
6. Strictly validates the full Qwen 0.22.3 wrapper + companion shape
   (verdict line, required counts, report path, per-finding required and
   optional renderer fields) and the exact finding vocabulary, computes a
   disposition (`pass`, `blocked`, `needs_human`, `stale`, `review_error`),
   and writes full evidence. If persisting `result.json` fails, the run is
   `review_error` and the original disposition is never emitted.

## What it never does

This skill is **non-posting and makes no intended tracked application changes**.
The native Qwen process may create untracked local artifact files; those are
expected, recorded, and left in place (tracked or staged repository changes
during the review are detected and reported as `review_error`, never
repaired automatically).

It never:

- edits application source or commits/pushes/merges anything;
- posts GitHub reviews or comments;
- calls `ao send`, changes AO configuration, or creates AO workers;
- repairs findings;
- starts a timer, daemon, poller, or scheduler;
- repeats or schedules a review automatically;
- merges a PR.

After reporting the result, the operator decides what happens next.

## Project-specific risk extension

The Skill is project-neutral. Global harness contract files are always high-risk.
A repository can add stack- or product-specific paths and labels through
`.qwen/review-config.json`. The file is optional, additive only, schema-versioned,
bounded, read from the tracked repository `HEAD`, and fail-closed. Untracked
local policy is rejected. See `references/policy.md` for normative semantics and
`references/project-risk-config.schema.json` for the machine-readable schema.

## Result transport (v0.2.5)

The semantic dispositions (pass, blocked, needs_human, stale,
review_error) ride inside the validated `result.json`, not in the process
exit code, for the two envelope transports. Every persisted
`result.json` (contractVersion=6) carries the identity fields
`contractVersion`, `reviewKey`
(`owner/repo#<PR>@<EXPECTED-40-CHAR-SHA>`), `attemptId` (the unique
run-directory name for this invocation), `semanticExitCode` (the
disposition's native exit code: 0 pass, 3 review_error, 4 stale, 5 blocked,
6 needs_human), and `invocationTransport` (`direct`,
`background-envelope`, or `monitor-envelope`).

- `direct`: the process exit code is the semantic exit code
  (0/2/3/4/5/6).
- `background-envelope` (low-level/manual): the process exits 0 only when
  this exact invocation persisted and validated a trustworthy, non-empty
  `result.json`; the result must be retrieved explicitly from the task's
  output file. Invalid arguments exit 2, and every other failure (crash,
  signal/interruption, absent/empty/stale/malformed result, persistence
  failure, attempt-identity mismatch) exits nonzero with no trustworthy
  result.
- `monitor-envelope` (the real-review transport): same result contract and
  same exit policy as `background-envelope`, but the helper is run as a
  child of Qwen's native `monitor` tool and streams bounded, deterministic
  protocol events to stdout — single-line JSON prefixed with
  `AO_PR_REVIEW_EVENT=`:

  ```text
  {"type":"keepalive","elapsedSeconds":<monotonic-seconds>}
  {"type":"heartbeat","elapsedSeconds":<monotonic-seconds>,"stage":"<optional-stage>","agentsStarted":<optional-int>,"agentsCompleted":<optional-int>,"lastActivityAt":"<optional-iso-time>"}
  {"type":"complete","resultJson":"<abs-path>","disposition":"<d>",
   "semanticExitCode":<0|3|4|5|6>,"reviewKey":"owner/repo#PR@sha",
   "attemptId":"<run-dir-name>"}
  {"type":"transport_error","semanticExitCode":<2|3>}
  ```

  Qwen 0.23.0 hard-caps monitor idle timeout at 600000 ms, so a fixed
  480-second transport-only `keepalive` prevents idle termination. A richer
  960-second `heartbeat` may carry bounded observational stage/agent-count
  metadata parsed mechanically from the inner Qwen transcript; `complete` is emitted only after the current-run
  `result.json` is durably persisted and revalidated for this exact
  invocation, with metadata that exactly matches that result; heartbeat
  generation stops before the terminal event; cancellation never fabricates
  a result and never emits `complete`. In monitor mode stdout carries only
  protocol events — never finding text, native Qwen progress, or any
  externally supplied prose.

In direct and background-envelope transports the helper's final stdout
always ends with exactly these lines, corresponding to the validated
current-run result:

```text
DISPOSITION=<pass|blocked|needs_human|stale|review_error>
SEMANTIC_EXIT_CODE=<0|3|4|5|6>
RESULT_JSON=<absolute-path>
```

Transport exit 0 means "the review operation produced a trustworthy
result" — not "the review passed". A Monitor `completed` status is a
transport fact, never a semantic PASS. Transport success is never inferred
from an old result file or from loosely parsed stdout; the validated
current-run result is the only evidence.

## Monitor execution and ownership (v0.2.5)

Every real review launches the helper through Qwen's native `monitor` tool
in monitor-envelope transport, with the current repository worktree as
`directory`, `idle_timeout_ms: 600000`, and `max_events: 128`; the command
is `python3 <skill-dir>/scripts/run_explicit_review.py --monitor-envelope
--args-file <injected-path>`, never run in the foreground, never through
`run_shell_command`, and never with a trailing `&`, `nohup`, a second
watcher, a foreground shell call, or a scheduler. After launch the session
reports `REVIEW STARTED`, the monitor ID, and that the dedicated AO reviewer
session remains available, without claiming the PR or the expected head SHA;
the exact PR, expected head SHA, effort, findings, and disposition come from
the validated `result.json` named by the `complete` event.

- Qwen's native `monitor` streams each bounded protocol event from the
  helper back as a notification to the owning session; the helper's
  480-second transport keepalive stays below the monitor's 600000 ms idle
  timeout; richer progress heartbeats are emitted every 960 seconds.
- The monitor's `completed`/failed/cancelled status is a transport fact: the
  semantic verdict comes only from the validated `result.json`, and a failed
  or cancelled monitor, a missing or invalid `complete` event, or an invalid
  or missing result file is `REVIEW ERROR`, never a code verdict.
- Qwen background shells settle silently: they remain observable in `/tasks`
  and their output file, but they do not push a completion notification that
  resumes result handling, so real reviews never rely on that path.
- Terminating the Qwen session can terminate its in-flight monitor review.
- An interrupted or cancelled review has no valid verdict and must later be
  re-run fresh against the exact current head SHA.
- For manual qualification, invoke this skill inside a dedicated AO reviewer Task session — not the main orchestrator and not the implementation worker.
- AO orchestrator rules create and supervise the dedicated reviewer Task;
  this Skill implements semantic-review execution only and does not own AO
  scheduling, routing, repair, or publication.
- The internal native Qwen review agents remain Qwen implementation
  details.

## Layout

```text
VERSION                                   0.3.3
SKILL.md                                  skill definition (operator or dispatched AO reviewer)
scripts/run_explicit_review.py            deterministic helper (Python 3, stdlib only)
references/contract.md                    Qwen 0.22.3 artifact + result contract
references/policy.md                      effort policy v2 + disposition policy
tests/test_review_helper.py               unit tests (fake gh/qwen, no network)
tests/fixtures/qwen-review-artifact-v1.json  official-shaped companion fixture
```

## Installation

Install an exact copy of this tree (excluding `.git`) at:

```text
~/.qwen/skills/ao-pr-review
```

with directories `0755`, Markdown/JSON/Python files `0644`, and
`scripts/run_explicit_review.py` `0755`.

The versioned ZIP (e.g. `ao-pr-review-v0.3.3.zip`, built with
`git archive` from the committed HEAD) is a **source archive**: it
captures the committed tree and records no live state. Installation from
it is a plain copy plus an explicit restoration of executable mode
(`0755` on `scripts/run_explicit_review.py`), as described above.

Run the tests from either the source repository or the installed copy:

```text
python3 -m unittest discover -s tests -v
```

The helper's `help` command must exit `0` without invoking `gh` or `qwen`:

```text
python3 scripts/run_explicit_review.py help
```

## Evidence

All state lives under `~/.local/state/ao-pr-review/`
(`AO_PR_REVIEW_STATE_DIR` overrides this for maintenance/test use only):

```text
locks/<owner>__<repo>/pr-<number>.lock
runs/<owner>__<repo>/pr-<number>/<UTC-timestamp-with-microseconds>-<suffix>/
    preflight.json qwen-run.json qwen-stderr.log review.md review.json result.json
```

Every attempted run after successful PR resolution gets a unique run
directory. JSON files are written atomically (temp file + rename). SHA-256
hashes of the preserved raw artifacts are recorded in `result.json`.

## Helper exit codes

Direct transport (`invocationTransport=direct`):

```text
0 = pass or help
2 = usage error
3 = review_error
4 = stale
5 = blocked
6 = needs_human
```

Envelope transports (`invocationTransport=background-envelope` or
`monitor-envelope`):

```text
0 = a trustworthy, validated, non-empty result.json persisted by this
    invocation — any disposition (pass, blocked, needs_human, stale,
    review_error); the semantic verdict is the result's
    disposition/semanticExitCode, never the process exit code
2 = usage error
3 = crash, signal/interruption, absent/empty/stale/malformed result,
    persistence failure, or attempt-identity mismatch — no trustworthy
    result from this invocation
```

In monitor-envelope mode the process additionally emits a terminal protocol
event before exiting (`complete` on exit 0, `transport_error` on 2/3), and
stdout carries only `AO_PR_REVIEW_EVENT=` lines.

In direct and background-envelope transports the final stdout always ends
with:

```text
DISPOSITION=<value>
SEMANTIC_EXIT_CODE=<0|3|4|5|6>
RESULT_JSON=<absolute-path>
```

(`RESULT_JSON` is empty whenever no trustworthy `result.json` was
persisted, including a write failure after the evidence directory was
created; such a run is `review_error`.)
The Skill has no AO automation or routing. Global `orchestratorRules` owns reviewer-Task creation, coordination, lifecycle routing, and publication authorization.
