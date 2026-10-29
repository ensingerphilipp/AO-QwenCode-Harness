# Host Installation and New-Project Initialization

This document defines the supported installation path for the AO + Qwen Code harness. It covers host installation and onboarding a repository either before AO registration or after adding it through the AO GUI. The onboarding Skill preserves existing AO identity and unrelated configuration. Broader cutovers of an established harness deployment use the migration procedure.

## Prerequisites

The target user must have `python3`, `git`, `gh`, and `qwen` in `PATH`. The AO AppImage must be present and executable at `~/.ao/agent-orchestrator.AppImage` or be discoverable via the registered AO desktop launchers. An existing `ao` CLI is not required: the installer probes the AppImage-bundled CLI directly. Qwen must already have a working model/provider/auth configuration for that user, and GitHub CLI authentication must be appropriate for the repositories the harness will review and publish to.

The harness does not install the AO GUI/AppImage or Qwen themselves and never writes provider credentials, API keys, tokens, model endpoints, or `~/.qwen/settings.json`. On a user-scoped Linux AO AppImage install, it installs a stable `~/.local/bin/ao` launcher and synchronizes the bundled CLI from `~/.ao/agent-orchestrator.AppImage` into a private cache. The launcher rechecks the image metadata at every CLI invocation and refreshes the cached binary after GUI updates. Other AO installations are not silently uninstalled.

The semantic-review runtime requires AO project sessions with `spawn --mode chat`, directed `ao send --steer` follow-up turns, and Qwen ACP support (`qwen --acp`). Installation verification probes those capabilities. Native `qwen review run` is no longer a harness prerequisite; the persistent reviewer executes `/review` directly in its own Chat/ACP conversation.

AO two-phase publication also requires the operator-scope Qwen setting `review.comment` to be disabled/false. Qwen treats a standing `review.comment: true` exactly like `/review --comment`, which would publish during the initial verdict turn before AO authorization. The installer and installation verifier probe the effective native `parse-args` result and fail closed if automatic commenting is enabled; they do not rewrite Qwen settings.

### Operator-owned Qwen review runtime tuning

Native Qwen review workflow concurrency and no-progress detection are Qwen runtime concerns, not harness policy. The harness therefore does not write or override these values. Operators running constrained or locally queued inference should set them in the Qwen host environment (for example `~/.qwen/.env`) before the Qwen/AO session starts:

```text
QWEN_CODE_MAX_TOOL_CONCURRENCY=<host-capacity>
QWEN_CODE_MAX_WORKFLOW_CONCURRENCY=<host-capacity>
QWEN_CODE_WORKFLOW_STALL_SECONDS=<host-appropriate-seconds>
```

`agents.maxParallelAgents` in Qwen settings controls ordinary/background Agent-tool subagents; native review workflow concurrency is resolved separately. For native review, `QWEN_CODE_MAX_WORKFLOW_CONCURRENCY` takes precedence, with `QWEN_CODE_MAX_TOOL_CONCURRENCY` used as its fallback. `QWEN_CODE_WORKFLOW_STALL_SECONDS` controls how long a workflow agent may produce no workflow-visible progress before Qwen aborts and retries that dispatch. These are host-specific operational limits and must not be copied into project policy or treated as portable harness defaults.

On the current local-inference deployment (2026-10-13), the concurrency value is `4` because two SGLang TP2 estates each permit two concurrent requests (**four logical inference slots**). Each request has a 262,144-token configured context limit; this is not a 262,144-token output limit. The deployed operator settings are therefore:

```text
QWEN_CODE_MAX_TOOL_CONCURRENCY=4
QWEN_CODE_MAX_WORKFLOW_CONCURRENCY=4
QWEN_CODE_WORKFLOW_STALL_SECONDS=600
```

The value `4` is derived from that host's four logical inference slots, not from an AO/Qwen harness rule. A host with different inference capacity should choose a matching concurrency limit. Likewise, the `600`-second stall window is local runtime tuning for slow queueing/compaction/inference and is not a universal review-policy value. Environment changes apply only to Qwen processes started after the change; an already-running native review keeps the environment it inherited at launch.

## Install host-global assets

From a clean checkout of this repository:

```bash
bash install/install-host.sh --dry-run
bash install/install-host.sh
bash install/verify-install.sh
```

The installer deploys `~/.local/bin/ao` plus a Python launcher helper at `~/.local/lib/ao-qwen-code-harness/ao_cli.py`. The launcher extracts the AppImage-bundled CLI on first use or after an AppImage update; it never runs or changes a different AO version. An existing unmanaged `ao` symlink requires explicit `--replace` and is backed up. If `~/.profile` does not already mention `~/.local/bin`, the installer adds a guarded PATH entry for future login shells; it refuses to overwrite a symlinked or nonregular profile. AppImage absence or failed extraction fails closed; the GUI/AppImage is not installed or updated by the harness.

The installer deploys the global Qwen context, AO worker/orchestrator rules, semantic-review publication policy, the human-only `ao-init-project`, `ao-semantic-review-override`, and `ao-replace-orchestrator` Skills, the orchestrator refresh utility, and the deterministic host-global semantic-review admission queue to standard user locations under `$HOME`.

Installation state is recorded at `${XDG_STATE_HOME:-$HOME/.local/state}/ao-qwen-code-harness/install-manifest.json`. A repeated identical install is a no-op. A target previously managed by the installer may be upgraded when its installed hash still matches the manifest.

When upgrading a pre-queue harness installation, the installer retains the legacy lock check and refuses while an old `ao-pr-review` lock is held. For the v0.3.x → v0.4.0 review-backbone cutover, operators must also ensure no old reviewer/Monitor lifecycle is in flight before replacing host-global rules: persistent reviewer semantics and the retired helper must never be mixed within one review.

If an unmanaged or locally modified target differs from the harness source, installation fails. `--replace` is explicit operator authorization to back up that target under the harness state directory and replace it.

## Onboard through Qwen or the AO GUI

After adding a repository in AO's GUI and selecting Qwen, invoke `/ao-init-project` in the orchestrator chat. For a repository not yet registered with AO, invoke `/ao-init-project /absolute/path/to/repository` from a Qwen session. The Skill is explicitly user-invoked and supports both paths.

The Skill uses `install/onboard_project.py` from the harness checkout recorded in the host installation manifest. Keep that checkout available; the installed wrapper reports a missing checkout instead of guessing another source. The helper's `inspect` action compares Git common directories, so linked worktrees and path aliases reuse the correct AO project. Unknown registration state or conflicting IDs stop the operation.

The agent inspects the repository using the existing prompt/schema, resolves missing product and architecture decisions with the human, and saves the inspection JSON outside the worktree. An unregistered project needs an agreed project ID. A plain folder must first become a Git repository with an initial commit; existing files are reviewed before staging, and an empty product needs an agreed initial scaffold and meaningful verification.

Use a separate linked onboarding worktree so a squash/rebase merge cannot leave the running orchestrator on a divergent branch. The `prepare` action renders the baseline using the existing templates. It preserves existing contracts by refusing conflicting files and symlinked targets. The checkout must be clean except AO's untracked `.qwen/settings.json`. The Skill verifies and commits the reviewed baseline through the project's normal PR workflow; invoking the Skill does not authorize merging that PR.

Before activation, configure and fetch the project remote, record its confirmed default branch, and verify at least one required deterministic CI check under the repository policy. A local-only repository can be prepared but needs a remote for the orchestrator refresh hook. After the accepted baseline is available on the remote default branch, `configure` verifies those files and the host installation, then rechecks registration. It reuses an existing project, or registers the primary checkout only when no matching project exists. It applies the two Qwen role selections, the two global rule loaders, and the refresh hook through AO's CLI, preserving other configuration and existing hooks. An absent default-branch setting is filled from the resolved inspection; a conflicting explicit value is refused. Tracker intake is unchanged. An existing explicit semantic-review choice is preserved; new baselines default to enabled.

The old AO config is saved in a private file before replacement. A partial failure leaves the registration available for inspection and retry; the helper never removes/re-adds a project or restarts an orchestrator. AO resolves configuration when sessions spawn, so an existing orchestrator must refresh its original checkout and explicitly read the global rules to continue. The Skill reports completion only after default-branch and configuration verification. It is not a general migration tool for conflicting existing contracts or workspace projects.

## Inspect a repository before initialization

Run the read-only task in `templates/prompts/inspect-project.md` and persist its JSON result. The result must conform to the current `config/project-inspection.schema.json` (schemaVersion 2) and contain no unresolved `decisionsRequired` entries before initialization. Schema v2 removes the retired review-risk/effort payload.

Human decisions identified during inspection must be resolved in the inspection result; `init-project` does not invent values to make rendering succeed.

## Initialize a new project directly from the command line

The repository must be clean and the AO project ID must not already be registered:

```bash
bash install/init-project.sh \
  --repo /absolute/path/to/repository \
  --inspection /absolute/path/to/inspection.json \
  --project-id my-project
```

Use `--semantic-review disabled` only for an explicit project decision. Semantic review defaults to enabled. Use `--tracker-assignee USER` only when issue intake is intentionally enabled for that project.

`--render-only` creates the repository baseline without AO registration and is useful for qualification or a separately controlled registration step.

Initialization renders the production project contracts, lifecycle config, deterministic `scripts/verify`, GitHub verification workflow, and harness `.gitignore` additions. Existing conflicting project files are never overwritten; migration handles repositories that already carry contract files.

After rendering, the script registers the new project with `ao project add` and configures Qwen workers/orchestrator, short global-rule loaders, the inferred/inspected default branch, and the orchestrator refresh post-create hook through `ao project set-config`.

After registration, initialization runs project-aware installation verification. The reusable command is `bash install/verify-install.sh --project-id <id>`; it asserts the exact short `agentRules`/`orchestratorRules` loaders, Qwen worker/orchestrator selection, and refresh hook stored by AO.

If AO configuration fails after a new registration is created, the initializer removes that newly created registration instead of leaving a partially configured AO project.

## Verification and ownership

Run `bash scripts/verify` in the initialized project and commit the resulting baseline through the project's normal PR workflow. Host installation and project initialization do not commit or push repository changes.

The original `init-project.sh` command remains a registration-first CLI path and refuses an existing AO project. Use `ao-init-project` for GUI-added projects whose baseline is not yet established. Use migration for broader changes to an established deployment; neither initializer silently overwrites conflicting project contracts.
