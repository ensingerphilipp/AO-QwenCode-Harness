# ao-pr-review contract (contractVersion=6)

## Invocation

Skill (manual only):

```text
/ao-pr-review <PR-number-or-URL> <EXPECTED-40-CHAR-HEAD-SHA> [auto|medium|high]
/ao-pr-review help
```

Helper:

```text
python3 <skill-dir>/scripts/run_explicit_review.py --args-file <injected-path>
python3 <skill-dir>/scripts/run_explicit_review.py <target> <sha> [auto|medium|high]
python3 <skill-dir>/scripts/run_explicit_review.py --background-envelope --args-file <injected-path>
python3 <skill-dir>/scripts/run_explicit_review.py --background-envelope <PR-number-or-URL> <sha> [auto|medium|high]
python3 <skill-dir>/scripts/run_explicit_review.py --monitor-envelope --args-file <injected-path>
python3 <skill-dir>/scripts/run_explicit_review.py --monitor-envelope <PR-number-or-URL> <sha> [auto|medium|high]
python3 <skill-dir>/scripts/run_explicit_review.py help
```

- Qwen Code 0.22.3 writes slash-command arguments verbatim to a
  session-private file and injects its path as
  `<skill-args-file>...</skill-args-file>`. The model passes only that path;
  it never reconstructs arguments from conversation.
- The args file is read once, at most 4096 bytes, symlinks are refused,
  contents are parsed with `shlex.split`, and raw contents are never
  printed. After parsing, only exactly `help` or exactly 2 or 3 review
  arguments are accepted. Missing, unreadable, empty/stale-looking,
  malformed, one-token non-`help`, or otherwise unsupported files fail
  closed with a usage error (exit 2) without invoking `gh` or `qwen`.
  There is no guessing.
- The expected head SHA must be exactly 40 lowercase hexadecimal characters.
- `help` exits 0, prints usage, and invokes neither `gh` nor `qwen`.
- Every real review launches the helper through Qwen's native `monitor` tool
  in monitor-envelope transport; see "Result transport (v0.2.5)" and
  "Monitor execution and ownership (v0.2.5)".

## Repository binding

- The PR is resolved with `gh pr view` requesting at least: `number`, `url`,
  `state`, `isDraft`, `headRefOid`, `additions`, `deletions`, `changedFiles`,
  `files`, `labels`, `title`, `body`.
- Real `gh` 2.98.0 `--json files,labels` output is flattened arrays —
  `"files": [{"path": "..."}, ...]` and
  `"labels": [{"name": "..."}, ...]` — not GraphQL-style
  `{"nodes": [...]}` wrappers. Every valid path/name is extracted for risk
  selection; missing, wrong-type, or malformed `files`/`labels` metadata is
  recorded as incomplete risk metadata and forces the selected effort to
  high. If `changedFiles` exceeds the number of valid file paths returned,
  the file list is treated as incomplete (high). There is no special
  assumption about a specific file count.
- The returned canonical URL must match
  `https://github.com/<owner>/<repository>/pull/<number>`; owner/repository
  are derived from that URL. `headRepository` is never used as the base
  identity — fork PRs are attributed and locked under the base repository.
- The current repository comes from `gh repo view`. A mismatch is
  `review_error` with an instruction to use an orchestrator in the correct
  repository.
- The canonical URL (not a bare number) is used for every later `gh` and
  `qwen review run` command.

## Preconditions (before any review inference)

1. PR resolved.
2. `state == "OPEN"`.
3. `isDraft == false`.
4. Resolved `headRefOid` equals the expected SHA.
5. `gh pr checks <canonical-PR-URL> --required --json name,state,bucket` —
   the full snapshot (command, exit status, all checks, stderr/parse error)
   is preserved as evidence. The structured check list is parsed from
   stdout regardless of the gh exit code (gh exits nonzero for failed or
   pending required checks while stdout still carries the JSON array).
   Missing or malformed output — gh failure, unparseable stdout, or no
   valid JSON array — is `review_error` (fail closed).
6. At least one required DETERMINISTIC check: the structured check list
   excluding ONLY the exact commit-status context `ao/semantic-review`
   (exact string match on `name`). Similarly named contexts (e.g.
   `ao/semantic-review-v2`, `semantic-review`) are never excluded, and
   entries that are not objects or whose `name` is not that exact string
   are never excluded — they count as deterministic and fail closed in
   precondition 7. An empty deterministic set — no required checks at all,
   or only the excluded context — is `review_error`.
7. Every required DETERMINISTIC check has `bucket == "pass"`. The excluded
   `ao/semantic-review` entry never gates this precondition in any state
   (absent, pending, failure, or error/cancel). The gh aggregate exit code
   is not the pass/fail gate: a nonzero exit with a parseable list whose
   deterministic set is non-empty and fully passing allows the review.
8. Initial `headRefOid` re-read after the CI checks; must still equal the
   expected SHA (early refusal and evidence).
9. Bounded patch inspection and deterministic effort selection.
10. Repository guard baseline: the repository must have **no tracked or
    staged changes** before Qwen launches; otherwise `review_error` without
    launching Qwen. (A pre-dirty tracked file could change again without
    altering its porcelain status line, so a dirty baseline is refused
    instead of diffed.)
11. The per-base-repository/per-PR lock is acquired non-blockingly before
    Qwen and released in a `finally`. A concurrent same-PR attempt fails
    without launching Qwen and never overwrites another run's evidence.
12. `qwen --version` once, for evidence only.
13. Local repository identity snapshot, while still holding the lock and
    before the final head read: the repository must have no tracked or
    staged changes; the local `HEAD` commit and the symbolic branch/ref
    (detached `HEAD` recorded as `detached-HEAD@<sha>`) are recorded. A
    failure to read the local identity before launch is `review_error`
    without launching Qwen.
14. **Final** pre-review `headRefOid` read: the last head read, occurring
    after CI validation, patch inspection and effort selection, repository
    guard setup, lock acquisition, `qwen --version`, and the local identity
    snapshot. It must be valid and exactly equal to the expected SHA; the
    next external operation after it succeeds is the single
    `qwen review run`. Head movement during patch inspection or
    preparation is refused before Qwen.

After Qwen finishes, the head is re-read. If it cannot be retrieved or is
not a valid 40-character SHA the run is `review_error`; if it differs from
the expected SHA the run is `stale`. The local repository identity is also
re-read: the tracked/staged status, the local `HEAD`, and the branch/ref.
Any tracked or staged entry, any local `HEAD` change (which catches a
child process that commits and leaves a clean worktree), or any branch/ref
change makes the run `review_error`; a failure to read the local identity
after the review is also `review_error`. Nothing is reverted or repaired
automatically. The before/after identity snapshots (tracked status, `HEAD`,
ref) are persisted in `result.json`. The earlier head values are never
reused. The three recorded heads — at resolution, immediately before, and
immediately after — are kept as distinct fields.

## Native command

Exactly one fresh native semantic review, with stdout and stderr captured
separately:

```text
qwen review run <canonical-PR-URL> \
  --effort <selected-medium-or-high> \
  --json \
  --fail-on request-changes \
  --approval-mode yolo \
  --timeout-minutes <240|480>
```

The native timeout is deterministic per selected effort (v0.2.3):
`medium` -> 240 minutes, `high` -> 480 minutes. The Python
`subprocess.run` wrapper timeout is the native budget plus 600 seconds of
cleanup grace. Both values are persisted in `result.json` as
`reviewTimeoutMinutes` and `wrapperTimeoutSeconds`. A native timeout
(wrapper `timedOut`, or an exit that is neither 0 nor 3) or a wrapper
timeout (the subprocess is killed) remains `review_error`.

`--comment` and `--resume` are forbidden. `qwen --version` is called once
for evidence and is not a semantic review.

## Wrapper (qwen-run.json)

The wrapper is the JSON object emitted by the native command. Resolution
order: the entire stdout, then the last valid JSON-object line of the same
stdout. The wrapper is sourced **only from the current run's stdout** — a
pre-existing `qwen-run.json` file in the repository root must never
influence a new run and is never consulted. Empty or invalid current
stdout is `review_error` even if such a stale file exists. The run
directory's `qwen-run.json` always preserves the raw stdout verbatim.

Required wrapper fields:

```text
object (JSON object)
completed == true
timedOut == false
reportPath: non-empty string ending in .md
event: APPROVE | COMMENT | REQUEST_CHANGES
baseEvent: APPROVE | COMMENT | REQUEST_CHANGES
cappedBy: array of strings
```

Process exits `0` and `3` are possible completed semantic outcomes and still
require full parsing — exit 3 is never an early return. Any other exit is
`review_error`, with all available artifacts preserved.

## Companion (review.json)

The companion path is `reportPath` with its final `.md` replaced by `.json`.
Native artifacts are copied into the run directory as `review.md` and
`review.json`; raw files are preserved even when later validation fails.

Required companion fields (full Qwen 0.22.3 shape):

```text
schemaVersion == 1   (exactly the integer 1; any other value, including
                      booleans, is a contract change)
target == "pr-<resolved-number>"
effort == selected effort
verdict is an object
verdict.event: APPROVE | COMMENT | REQUEST_CHANGES
verdict.baseEvent: APPROVE | COMMENT | REQUEST_CHANGES
verdict.cappedBy: array containing only strings
verdict.verdictLine: non-empty string
counts: required object
  total: non-negative integer
  bySeverity: object with non-negative integers for
             Critical, Suggestion, "Nice to have"
  byConfidence: object with non-negative integers for high, low
  held: non-negative integer
  byOutcome (optional): object with non-negative integers for
            fixed, skipped, no_change_needed
markdownReportPath: required relative path (no leading /) under
                    .qwen/reviews/, with no .. segment, ending in .md
findings is an array
```

A missing, invalid, or wrong-type required field — including
`verdict.event`, `verdict.baseEvent`, `verdict.cappedBy`,
`verdict.verdictLine`, `counts`, or `markdownReportPath` — is
`review_error` and can never produce `pass`, `blocked`, or `needs_human`.
Unknown fields are preserved verbatim. Once the wrapper and companion
fields are independently valid, `event`, `baseEvent`, and `cappedBy` are
cross-checked unconditionally; any disagreement is `review_error`. The
native `cappedBy` is preserved exactly; `medium-effort` is never
synthesized.

Exit/event consistency:

```text
exit 3 requires event == REQUEST_CHANGES
event == REQUEST_CHANGES (with --fail-on request-changes) requires exit 3
```

## Finding vocabulary (exact, case-sensitive)

```text
severity:   Critical | Suggestion | Nice to have
confidence: high | low
source:     review | build | test | probe | lint
outcome (optional): fixed | skipped | no_change_needed
```

Unknown severity, confidence, source, or outcome vocabulary is a contract
change, therefore `review_error` — never an assumed pass. Severity is
never reinterpreted from prose (a Suggestion never becomes a blocker
because its text says `MUST`).

Every finding must be an object with:

```text
id: non-empty string
severity, confidence, source: from the exact vocabulary above
summary, shortSummary, failureScenario: non-empty strings
locations: non-empty array of objects, each with
  file: non-empty string
  line (optional): positive integer when present, never a boolean
  anchor (optional): non-empty string when present
```

Optional renderer fields, validated only when present:

```text
witness, suggestedFix, category, outcomeNote: non-empty strings
assetFiles, assets: arrays of non-empty strings
heldByMeasurement: object with a non-empty file
outcome: from the outcome vocabulary
```

Every field is preserved verbatim, including unknown fields. When any
finding carries an `outcome`, every finding must carry one (partially
populated outcomes are `review_error`), and all values must be from the
vocabulary.

Disposition semantics:

- findings without an outcome are unresolved;
- `skipped` remains unresolved;
- `fixed` and `no_change_needed` are preserved but are not unresolved
  blockers.

## Project risk configuration

The optional repository file `.qwen/review-config.json` extends deterministic
effort selection. Its schema and matching semantics are normative in
`policy.md` (effortPolicyVersion=2). The helper resolves it from the verified
repository root before Qwen inference and reads only the tracked blob at
repository `HEAD`; mutable or untracked local content never supplies review
policy. If present in `HEAD`, it must be a bounded regular file (not a symlink)
and must pass complete schema and semantic validation. An untracked local file
at the same path, invalid configuration, or unreadable tracked policy is
`review_error`. Repository rules are additive and cannot remove global
high-risk rules.

`result.json` records `projectRiskConfig` with the repository-relative config
path, whether it was present, and its SHA-256 when present. The hash provides
provenance without copying repository policy content into runtime evidence.

## Disposition

Allowed: `pass`, `blocked`, `needs_human`, `stale`, `review_error`, applied
in the strict order defined in `policy.md`. The helper never guesses
`pass`.

## Evidence

State root: `~/.local/state/ao-pr-review/` (override for
maintenance: `AO_PR_REVIEW_STATE_DIR`).

```text
locks/<owner>__<repo>/pr-<number>.lock
runs/<owner>__<repo>/pr-<number>/<UTC timestamp with microseconds>-<random suffix>/
    preflight.json
    qwen-run.json      (raw qwen stdout, verbatim)
    qwen-stderr.log    (raw qwen stderr, verbatim)
    review.md          (copied native report)
    review.json        (copied native companion)
    result.json
```

Every attempted run after successful PR resolution gets a unique directory
(microsecond UTC timestamp plus random suffix; no `exist_ok` reuse). JSON is
written atomically (temp file + `os.replace`). SHA-256 hashes of the
preserved raw artifacts are recorded in `result.json`.

`result.json` contains at least:

```text
contractVersion, effortPolicyVersion, repository, prNumber, prUrl,
reviewKey, attemptId, invocationTransport,
requestedEffort, selectedEffort, effortReasons, reviewTimeoutMinutes,
wrapperTimeoutSeconds, expectedHead,
observedHeadAtResolve, observedHeadBefore, observedHeadAfter,
requiredChecks, qwenVersion, startedAt, finishedAt, qwenExitCode,
completed, timedOut, event, baseEvent, cappedBy,
localIdentityBefore, localIdentityAfter, projectRiskConfig, findings, disposition,
semanticExitCode, error,
nextAction, artifacts, artifactSha256
```

## Result transport (v0.2.5)

The semantic dispositions (pass, blocked, needs_human, stale, review_error)
ride inside the validated `result.json`, not in the process exit code, for the
two envelope transports. Every persisted result (contractVersion=6) includes:

```text
reviewKey:         owner/repo#<PR>@<EXPECTED-40-CHAR-SHA>
                   (the stable identity of the reviewed target, used for
                   deduplication)
attemptId:         the unique run-directory name for this invocation
                   (<UTC timestamp with microseconds>-<random suffix>)
semanticExitCode:  the disposition's native exit code
                   (0 pass, 3 review_error, 4 stale, 5 blocked,
                   6 needs_human)
invocationTransport: direct | background-envelope | monitor-envelope
```

`direct` transport: the process exit code is the semantic exit code; the final
stdout ends with `DISPOSITION=`, `SEMANTIC_EXIT_CODE=`, and `RESULT_JSON=`.

`background-envelope` transport (low-level/manual): the process exits 0 only
when this exact invocation persisted and validated a trustworthy, non-empty
`result.json` (any disposition); the result must be retrieved explicitly from
the task's output file — Qwen background shells settle silently and do not
push a completion notification. Invalid arguments exit 2, and every other
failure (crash, signal/interruption, absent/empty/stale/malformed result,
persistence failure, attempt-identity mismatch) exits nonzero with no
trustworthy result.

`monitor-envelope` transport (the real-review transport): same result contract
and same exit policy as background-envelope, but the helper is run as a child
of Qwen's native `monitor` tool and streams bounded, deterministic protocol
events to stdout — single-line JSON prefixed with `AO_PR_REVIEW_EVENT=`. The
events are:

```text
{"type":"heartbeat","elapsedSeconds":<monotonic-seconds>}
{"type":"complete","resultJson":"<abs-path>","disposition":"<d>",
 "semanticExitCode":<0|3|4|5|6>,"reviewKey":"owner/repo#PR@sha",
 "attemptId":"<run-dir-name>"}
{"type":"transport_error","semanticExitCode":<2|3>}
```

Monitor event rules:

- Only protocol events are emitted — never finding text, native Qwen progress,
  or any externally supplied prose.
- A fixed 480-second `heartbeat` is emitted while the helper is running
  (transport liveness only, keeping the monitor's 600000 ms idle timeout from
  firing); heartbeats carry only the event type and a monotonic elapsed-second
  count. A 480-second heartbeat over the eight-hour high-effort budget fits
  well below the monitor `max_events` of 128.
- `complete` is emitted only after the current-run `result.json` is durably
  persisted and revalidated for this exact invocation, and its metadata
  (`resultJson`, `disposition`, `semanticExitCode`, `reviewKey`, `attemptId`)
  exactly matches that result. It is never emitted before durable persistence
  or for a stale or mismatched attempt.
- Heartbeat generation stops before the terminal event is emitted.
- Cancellation or process termination never fabricates a result and never
  emits `complete`; a failure without a trustworthy result emits
  `transport_error`.
- Transport exit 0 means "the review operation produced a trustworthy
  result" — not "the review passed". A Monitor `completed` status is a
  transport fact, never a semantic PASS. Transport success is never inferred
  from an old result file or loosely parsed stdout.

## Helper output and exit codes

Final stdout always ends with exactly:

```text
DISPOSITION=<pass|blocked|needs_human|stale|review_error>
SEMANTIC_EXIT_CODE=<0|3|4|5|6>
RESULT_JSON=<absolute-path>
```

`RESULT_JSON` is empty whenever no trustworthy `result.json` was persisted —
both for failures before an evidence directory could be created (e.g.
invalid arguments, unresolvable repository/PR) and for a write failure
after the evidence directory was created. If persisting `result.json`
fails, the run is `review_error` (exit 3), stdout ends with
`DISPOSITION=review_error` and an empty `RESULT_JSON=`, the original
pass/blocked/stale/needs_human disposition is never emitted, and the error
message names the failed evidence directory. No Python traceback is emitted
as the only result once an evidence directory exists.

Direct transport process exit codes:

```text
0 = pass or help
2 = usage error
3 = review_error
4 = stale
5 = blocked
6 = needs_human
```

Envelope transport process exit codes (background-envelope and
monitor-envelope):

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

Under monitor-envelope the process additionally emits a terminal protocol
event before exiting: `complete` naming the `result.json` path on exit 0, or
`transport_error` carrying the semantic code on 2/3; in monitor mode stdout
carries only `AO_PR_REVIEW_EVENT=` lines.

## Repository mutation guard

The local repository identity — tracked/staged status, local `HEAD`, and
branch/ref (detached `HEAD` as `detached-HEAD@<sha>`) — is recorded before
and after the native review using read-only git commands only
(`status`, `rev-parse`, `symbolic-ref`); the helper never adds, commits,
checks out, resets, cleans, or restores. Before Qwen launches, the
repository must have **no tracked or staged changes**; if any exist, the
run is `review_error` and Qwen is not launched. After Qwen finishes, the
identity is re-read: any tracked or staged entry, any local `HEAD` change
(a child process that commits and leaves a clean worktree is caught by the
`HEAD` comparison, not the status line), or any branch/ref change makes
the run `review_error`, and nothing is repaired or reverted
automatically. A failure to read the local identity at either point is
also `review_error`. Untracked/ignored review artifacts are expected and
ignored in both checks. The skill is non-posting and makes no intended
tracked application changes.

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
  480-second heartbeat keeps the monitor's 600000 ms idle timeout from
  firing during long reviews.
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
  this Skill implements no AO scheduling, routing, repair, or publication.
- The internal native Qwen review agents remain Qwen implementation
  details.

The dedicated AO reviewer Task entry point — explicitly created by the
orchestrator and invoking the positional monitor-envelope form — is specified
in `SKILL.md` under "AO reviewer Task entry point".
The Skill has no AO automation or routing. Global `orchestratorRules` owns reviewer-Task creation, coordination, lifecycle routing, and publication authorization.
