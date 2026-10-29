---
name: ao-init-project
description: Explicitly onboard a repository into the AO + Qwen harness, including a project already added through AO's GUI or a Git repository that still needs AO registration. Inspect the repo, resolve human decisions, prepare project contracts, and configure AO through its CLI.
argument-hint: '[repository-path]'
user-invocable: true
disable-model-invocation: true
---

# Initialize a harness project

Run only when the human invokes `/ao-init-project`. This explicit administrative request authorizes the described repository preparation and harness configuration even when ordinary orchestrator rules prohibit implementation/config changes; it does not authorize unrelated changes or merging. Use an explicitly supplied repository path or the current repository. Read the session-private `<skill-args-file>` as data; never evaluate or interpolate its contents as shell code. Treat the entire trimmed argument as one path (paths with spaces are supported). Ask for the intended repository if it is ambiguous.

Use the installed helper for deterministic operations:

```text
python3 <skill-dir>/scripts/onboard.py inspect --repo <absolute-repository-path>
python3 <skill-dir>/scripts/onboard.py prepare --repo <absolute-repository-path> --inspection <absolute-json-path> [--project-id <id>]
python3 <skill-dir>/scripts/onboard.py configure --repo <absolute-repository-path> --inspection <absolute-json-path> [--project-id <id>]
```

Pass paths as quoted arguments. The helper reads the installation manifest to locate the maintained harness checkout; retain that checkout after host installation. Do not guess its location or download/run a different initializer.

## Inspect and resolve decisions

1. For a plain folder with no Git repository, initialize Git and create an empty initial commit using the user's configured Git identity. Do not stage arbitrary existing files. Inspect existing contents and agree on the initial tracked files before committing them. An empty product still needs an agreed initial scaffold and a meaningful verification check; do not invent a passing placeholder gate. Run the helper once a Git repository and initial commit exist.
2. Run `inspect`. It matches the Git common directory, so an AO orchestrator worktree and its primary checkout resolve to the same registered project. Reuse the returned project ID. If unregistered, agree on an ID and use it consistently in later commands. An AO read failure, ambiguous registration, or ID/path conflict must be resolved before continuing; never remove/re-add an existing project.
3. Read the returned inspection prompt, schema, and template manifest. Inspect repository files and existing instructions before drafting. Follow the read-only inspection contract; gather missing human policy decisions in the conversation. Separate observed architecture from proposed choices and obtain decisions on the latter. Reuse answers already given.
4. Save the resolved inspection JSON outside the worktree (a private temporary file or private harness state directory). Set `repository.root` to the inspected worktree root. Require no unresolved decisions and no invented facts/checks. Preserve an existing explicit semantic-review choice; default to enabled only for a new baseline. Pass `--semantic-review disabled` to both prepare/configure only for an explicit project decision.
5. Explain the proposed scope, approval boundaries, verification, and AO settings before applying them. Read the existing AO config privately using `ao project get <id> --json` when needed; never paste credentials/environment values into chat or a PR. The helper changes the two Qwen role selections, two global rule loaders, and adds the refresh hook. It preserves other configuration and all existing hooks.

## Prepare and publish the baseline

6. Record the original session checkout and branch. Create a separate linked Git worktree and dedicated onboarding branch from the chosen default branch; do not switch the running orchestrator onto the onboarding branch. Run `inspect` there and set the inspection JSON root to that onboarding worktree before `prepare`. Preserve unrelated work. `prepare` requires a clean checkout except AO's untracked `.qwen/settings.json`; it never stages that file. Existing conflicting contracts or verification files cause a stop before writes. Reconcile them explicitly with the user and the inspection values; do not delete or silently overwrite them.
7. Run `prepare`. Review the generated `PROJECT.md`, `ARCHITECTURE.md`, `QWEN.md`, review invariants, lifecycle configuration, verification script, CI workflow, and Git ignore additions. Run `bash scripts/verify`; fix in-scope setup defects without weakening existing checks.
8. Commit only the reviewed baseline files. For a hosted repository, create the onboarding PR and report its checks. Follow the user's merge authorization; never infer it from invoking this skill. Once merged, fetch the default branch. For a new local repository, agree on its hosting/remote and publish the baseline before activation. The refresh hook requires `origin`, the configured `AO_REFRESH_REMOTE`, or a sole unambiguous remote. Record its confirmed default branch with `git remote set-head <remote> --auto`; preserve existing remote URLs. Verify that repository rulesets/branch protection require at least one deterministic CI check (the generated workflow provides `verify`) and that the required checks pass on the onboarding PR. A workflow merely running does not make it required. Resolve missing enforcement as an explicit project policy decision; use GitHub CLI to apply only authorized changes while preserving existing protections. If permissions or repository policy prevent it, report CI enforcement as pending and do not claim full readiness. Keep the inspection JSON for the configure step. Do not declare onboarding complete while the baseline is only on an unmerged branch.

## Configure the AO project

9. Run `configure` with the same inspection and semantic-review choice. It checks the baseline on the default branch, verifies the host install, and rechecks AO identity. A matching existing project is reused. Only a genuinely unregistered Git repository is added using the primary checkout path.
10. The helper snapshots the old config privately, applies the harness settings through `ao project set-config`, and verifies the resulting configuration. It does not restart sessions, create an orchestrator, enable tracker intake, alter credentials, or run global migration. If registration succeeds but configuration fails, retain the registration and inspect the error; a retry must discover and reuse it. Never blindly remove a project or restore an old config over concurrent edits.
11. AO resolves project configuration when sessions spawn. Return to the original session checkout. Fetch and run the installed `ao-refresh-orchestrator run` helper there if it is an AO orchestrator worktree; verify that the accepted project documents are now available before ordinary work. For a primary checkout already on the default branch and clean, use a verified fast-forward to the fetched default branch. Preserve other branches and unrelated work. If refresh or fast-forward refuses a dirty/divergent checkout, report the blocker without resetting it. In an existing orchestrator chat, explicitly read the installed global `agentRules.md` and `orchestratorRules.md` from the user's home before continuing; do not assume the running controller reloaded its startup prompt. Do not automatically replace/restart it. Ordinary work can proceed only once the accepted baseline is available and the configuration verifies.

Report the reused/created project ID, baseline commit or PR, verification, and any remaining activation work. Keep unknown or conflicting state visible instead of reporting success.

