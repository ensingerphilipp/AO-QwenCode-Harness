# Existing-Project Migration

This procedure migrates an existing AO + Qwen Code project to the generalized harness without reconstructing or discarding project-specific AO configuration.

## Safety model

Migration is split into two independently reviewable stages:

1. **Host/AO cutover** — install generalized host-global assets and merge harness-owned AO configuration fields.
2. **Repository contract migration** — update project-owned contracts and verification through the repository's normal branch/PR workflow.

The host/AO migration command never edits project source or contract files.

## Preconditions

Before host/AO cutover:

- `bash scripts/verify` passes in the harness repository;
- the target AO project resolves to the exact repository root supplied to the command;
- there are zero nonterminated AO sessions across all projects on the host;
- the target repository is clean, except generated `.qwen/reviews/` evidence may be present;
- the current AO project config can be read in full with `ao project get --json`;
- the operator has explicitly approved the cutover.

## Read-only preflight

Run without an apply flag:

```bash
bash install/migrate-project.sh \
  --project-id <AO_PROJECT_ID> \
  --repo /absolute/path/to/repository
```

This reports project identity, active AO sessions, repository status, and whether the repository state is migration-safe. It performs no host or repository mutation.

## Backup

`--backup-only` creates a private migration snapshot under the target user's XDG state directory. It records:

- the complete AO project object and config;
- repository HEAD and status;
- existing host-global AO/Qwen harness files;
- the installed `ao-pr-review` Skill;
- the host installer manifest;
- project contract/verification files for reference;
- SHA-256 hashes for copied files.

A backup is evidence and rollback input; it does not itself authorize cutover.

## Host/AO cutover

After explicit operator approval and a fresh successful preflight:

```bash
bash install/migrate-project.sh \
  --project-id <AO_PROJECT_ID> \
  --repo /absolute/path/to/repository \
  --apply-host
```

The command creates a fresh backup, installs and verifies generalized host assets, and replaces only harness-owned AO configuration fields:

- worker/orchestrator rule loaders;
- worker agent = `qwen`;
- orchestrator agent = `qwen`;
- the orchestrator refresh `postCreate` hook.

Unknown and project-specific AO config keys are preserved exactly. Existing non-refresh `postCreate` commands are retained.

If global installation or AO config update fails, automatic rollback restores host files, installer state, and the complete original AO project config. An incomplete rollback is reported as a separate failure and is never described as successful recovery.

## Repository contract migration

After host/AO cutover is qualified, migrate repository-owned policy in a normal project branch and pull request. Do not mutate these files directly as part of host installation.

Use the evidence-driven project inspection contract to compare current project policy with the generalized ownership model, then update only what is justified:

- shrink project `QWEN.md` to the repository-local entry point;
- remove generic/global rules from `PROJECT.md` and `.qwen/review-rules.md`;
- keep project truth in `PROJECT.md` and `ARCHITECTURE.md`;
- add `.agent-harness.json` and `.qwen/review-config.json`;
- remove the project-local semantic-review publication policy after the global policy is qualified;
- retain `scripts/verify` as the mechanical verification authority and CI as its wrapper;
- ignore generated `.qwen/reviews/` evidence.

Run project verification and the normal AO/CI/semantic-review lifecycle against the migration PR before deleting obsolete duplicated policy.

## Rollback boundary

Automatic rollback covers the host/AO cutover transaction. Repository contract changes are normal Git changes and are rolled back through Git/PR mechanisms, not by the host migration command.
