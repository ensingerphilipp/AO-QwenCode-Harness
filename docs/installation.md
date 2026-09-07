# Host Installation and New-Project Initialization

This document defines the supported installation path for the AO + Qwen Code harness. It covers fresh/user-scoped installation and initialization of a **new** AO project. Existing registered AO projects are migrated separately so unknown configuration is never replaced implicitly.

## Prerequisites

The target user must have `python3`, `git`, `gh`, `qwen`, and `ao` available in `PATH`. Qwen must already have a working model/provider/auth configuration for that user, and GitHub CLI authentication must be appropriate for the repositories the harness will review and publish to.

The harness does not install AO or Qwen themselves and never writes provider credentials, API keys, tokens, model endpoints, or `~/.qwen/settings.json`.

## Install host-global assets

From a clean checkout of this repository:

```bash
bash install/install-host.sh --dry-run
bash install/install-host.sh
bash install/verify-install.sh
```

The installer deploys the global Qwen context, AO worker/orchestrator rules, semantic-review publication policy, every host-global Skill under `global/qwen/skills/` (currently `ao-pr-review` and the human-only `ao-semantic-review-override`), and the orchestrator refresh utility to standard user locations under `$HOME`.

Installation state is recorded at `${XDG_STATE_HOME:-$HOME/.local/state}/ao-qwen-code-harness/install-manifest.json`. A repeated identical install is a no-op. A target previously managed by the installer may be upgraded when its installed hash still matches the manifest.

If an unmanaged or locally modified target differs from the harness source, installation fails. `--replace` is explicit operator authorization to back up that target under the harness state directory and replace it.

## Inspect a repository before initialization

Run the read-only task in `templates/prompts/inspect-project.md` and persist its JSON result. The result must conform to `config/project-inspection.schema.json` and contain no unresolved `decisionsRequired` entries before initialization.

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

Initialization renders the production project contracts, review-risk config, lifecycle config, deterministic `scripts/verify`, GitHub verification workflow, and harness `.gitignore` additions. Existing conflicting project files are never overwritten; migration handles repositories that already carry contract files.

After rendering, the script registers the new project with `ao project add` and configures Qwen workers/orchestrator, short global-rule loaders, the inferred/inspected default branch, and the orchestrator refresh post-create hook through `ao project set-config`.

After registration, initialization runs project-aware installation verification. The reusable command is `bash install/verify-install.sh --project-id <id>`; it asserts the exact short `agentRules`/`orchestratorRules` loaders, Qwen worker/orchestrator selection, and refresh hook stored by AO.

If AO configuration fails after a new registration is created, the initializer removes that newly created registration instead of leaving a partially configured AO project.

## Verification and ownership

Run `bash scripts/verify` in the initialized project and commit the resulting baseline through the project's normal PR workflow. Host installation and project initialization do not commit or push repository changes.

Do not use `init-project` to update an existing AO registration or retrofit an existing project contract. That operation requires migration because current project settings and files must be inspected and preserved deliberately.
