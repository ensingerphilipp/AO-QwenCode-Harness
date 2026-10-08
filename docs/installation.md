# Host Installation and New-Project Initialization

This document defines the supported installation path for the AO + Qwen Code harness. It covers fresh/user-scoped installation and initialization of a **new** AO project. Existing registered AO projects are migrated separately so unknown configuration is never replaced implicitly.

## Prerequisites

The target user must have `python3`, `git`, `gh`, `qwen`, and `ao` available in `PATH`. Qwen must already have a working model/provider/auth configuration for that user, and GitHub CLI authentication must be appropriate for the repositories the harness will review and publish to.

The harness does not install AO or Qwen themselves and never writes provider credentials, API keys, tokens, model endpoints, or `~/.qwen/settings.json`.

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

On the current local-inference deployment, the concurrency value is `3` because the host exposes **three available local inference slots**. The deployed operator settings are therefore:

```text
QWEN_CODE_MAX_TOOL_CONCURRENCY=3
QWEN_CODE_MAX_WORKFLOW_CONCURRENCY=3
QWEN_CODE_WORKFLOW_STALL_SECONDS=600
```

The value `3` is derived from that host's three inference slots, not from an AO/Qwen harness rule. A host with different inference capacity should choose a matching concurrency limit. Likewise, the `600`-second stall window is local runtime tuning for slow queueing/compaction/inference and is not a universal review-policy value. Environment changes apply only to Qwen processes started after the change; an already-running native review keeps the environment it inherited at launch.

## Install host-global assets

From a clean checkout of this repository:

```bash
bash install/install-host.sh --dry-run
bash install/install-host.sh
bash install/verify-install.sh
```

The installer deploys the global Qwen context, AO worker/orchestrator rules, semantic-review publication policy, every host-global Qwen Skill/support package under `global/qwen/skills/` (currently the non-invocable `ao-pr-review` protocol package and the human-only `ao-semantic-review-override` Skill), the orchestrator refresh utility, and the deterministic host-global semantic-review admission queue to standard user locations under `$HOME`.

Installation state is recorded at `${XDG_STATE_HOME:-$HOME/.local/state}/ao-qwen-code-harness/install-manifest.json`. A repeated identical install is a no-op. A target previously managed by the installer may be upgraded when its installed hash still matches the manifest.

When upgrading a pre-queue harness installation, the installer retains the legacy lock check and refuses while an old `ao-pr-review` lock is held. For the v0.3.x → v0.4.0 review-backbone cutover, operators must also ensure no old reviewer/Monitor lifecycle is in flight before replacing host-global rules: persistent reviewer semantics and the retired helper must never be mixed within one review.

If an unmanaged or locally modified target differs from the harness source, installation fails. `--replace` is explicit operator authorization to back up that target under the harness state directory and replace it.

## Inspect a repository before initialization

Run the read-only task in `templates/prompts/inspect-project.md` and persist its JSON result. The result must conform to the current `config/project-inspection.schema.json` (schemaVersion 2) and contain no unresolved `decisionsRequired` entries before initialization. Schema v2 removes the retired review-risk/effort payload.

Human decisions identified during inspection must be resolved in the inspection result; `init-project` does not invent values to make rendering succeed.

## Initialize a new project

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

Do not use `init-project` to update an existing AO registration or retrofit an existing project contract. That operation requires migration because current project settings and files must be inspected and preserved deliberately.
