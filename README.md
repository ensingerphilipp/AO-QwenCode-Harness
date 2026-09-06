# AO-QwenCode-Harness

Production-oriented reusable baseline for Agent Orchestrator (AO) with Qwen Code. The repository separates host-global AO/Qwen policy, project bootstrap contracts, semantic review, deterministic verification, lifecycle utilities, and installation/migration tooling.

## Current status

The global policy layers, generalized `ao-pr-review` Skill, project templates, verification profiles, evidence-driven project inspection contract, and orchestrator worktree refresh utility are implemented and regression-tested. Host-independent installation, migration tooling, and fresh-environment end-to-end qualification remain the active implementation phases.

The repository is intentionally private while the harness is being qualified for broader reuse.

## Core contracts

- `global/qwen/QWEN.md` — universal Qwen engineering behavior.
- `global/ao/rules/` — AO worker/reviewer and orchestrator lifecycle rules.
- `global/ao/policies/semanticReviewPublication.md` — GitHub semantic-review publication policy.
- `global/qwen/skills/ao-pr-review/` — exact-SHA semantic review engine and contract.
- `templates/project/` — production project-bootstrap template sources.
- `templates/verify/` — evidence-driven deterministic verification profiles.
- `templates/prompts/inspect-project.md` — read-only project-inspection contract.
- `lifecycle/ao-refresh-orchestrator` — project-neutral orchestrator worktree refresh.
- `docs/architecture-and-policy-ownership.md` — canonical ownership matrix and architecture decisions.

## Verification

Run the same deterministic repository gate used by CI:

```bash
bash scripts/verify
```

The gate is fail-fast and non-repairing. It runs all harness contract, integration, and semantic-review regression tests and validates tracked repository artifacts.

## Deployment model

Do not copy runtime state, secrets, AO databases, worktrees, Qwen sessions, caches, or generated review evidence into deployments. Active deployment artifacts live under `global/`, `templates/`, `lifecycle/`, `config/`, and `install/` as documented by the architecture record.

Production installation and new-project initialization are documented in `docs/installation.md`. Existing-project migration and fresh-environment end-to-end qualification remain separate controlled phases.
