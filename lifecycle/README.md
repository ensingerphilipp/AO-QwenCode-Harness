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
