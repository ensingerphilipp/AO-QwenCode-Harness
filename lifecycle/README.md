# Lifecycle Utilities

## `ao-refresh-orchestrator`

Core harness utility. AO invokes it from the project `postCreate` hook before the orchestrator Qwen session starts.

The utility is intentionally project-neutral. It derives the active project from `AO_PROJECT_ID`, AO worktree storage from `AO_DATA_DIR`, the current linked worktree from `cwd`, the Git remote from repository metadata, and the default branch from the remote HEAD.

It operates only inside `<AO_DATA_DIR>/worktrees/<project>/orchestrator/...`; worker worktrees and ordinary repositories are no-ops.

Safety behavior:

- requires the orchestrator path to be a linked Git worktree;
- requires a clean tracked/untracked worktree and no in-progress Git operation;
- refuses to operate when the worktree branch is the remote default branch;
- fetches only the remote default branch with `--no-tags`;
- permits only a fast-forward from the current orchestrator head;
- refuses ahead/diverged histories or concurrent worktree changes;
- records a per-project/per-worktree result under the XDG state directory;
- uses a non-blocking per-worktree lock and non-interactive bounded Git commands.

## Reboot reconciliation

`ao-reconcile` is **not** a core startup dependency. Reconciliation of AO session state after host reboot is operational recovery behavior with stronger side effects (session restoration, tmux interaction, waits, and AO-version sensitivity).

Keep it optional/manual unless later qualification proves a stable AO-supported recovery contract. It must not be installed into automatic startup by the baseline harness.

## `ao-review-queue`

Core deterministic host-global admission utility for semantic reviews. It serializes semantic-review execution across all managed projects for the harness user using one `flock`-protected, atomically written FIFO state file under the harness XDG state directory.

Only AO orchestrators use it. A queued ticket is inert machine state: no reviewer Task, Qwen Monitor, semantic process, polling loop, or recurring model message exists until the ticket is promoted. Promotion returns only the next ticket ID, review key, and owning orchestrator ID to the releasing orchestrator, which sends one minimal `REVIEW_SLOT_GRANTED` message to the recorded orchestrator session. The promoted orchestrator requalifies the exact PR SHA and deterministic CI before creating a reviewer.

Timeout resume requests release the current ticket and re-enter at the back of the same FIFO. Stale active tickets have no TTL or automatic recovery; manual `status` + explicit `cancel` is the fail-closed recovery path.
