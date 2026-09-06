# Harness Architecture and Policy Ownership

This document is the canonical architecture record for the AO + Qwen Code harness. It defines where policy belongs, which files are authoritative, why the boundaries exist, and how the repository is expected to evolve.

Update this document in the same commit whenever an architectural assumption, ownership boundary, baseline file, or deployment decision changes.

## Design goals

- One source of truth for each rule or protocol.
- Global behavior lives in host-global AO/Qwen locations.
- Product and repository truth lives in each project repository.
- Every managed project receives a consistent baseline contract.
- Deterministic verification is executable, not prose-defined.
- Semantic review is enabled by default and may be disabled per project.
- GitHub pull-request development is part of the baseline.
- The harness supports multiple technology stacks without weakening guidance.
- Installation must support the current host and a host-agnostic path.

## Architectural layers

| Layer | Owns | Must not own |
|---|---|---|
| Global Qwen | Universal engineering behavior and project-contract discovery | AO orchestration protocol or product-specific rules |
| Global AO worker | Worker/reviewer lifecycle, handoffs, routed repair behavior | Semantic disposition definitions or project architecture |
| Global AO orchestrator | Coordination, readiness, dispatch, routing, publication authorization | Product implementation or semantic-review policy internals |
| Global review Skill | Review execution contract, effort policy, semantic disposition | AO lifecycle routing or GitHub publication |
| Project contract | Goals, scope, HITL, public contracts, architecture, project invariants | Generic host-wide behavior |
| Deterministic verification | Exact mechanical checks | Semantic policy or product decisions |
| Runtime state | Sessions, worktrees, evidence, caches | Canonical configuration or source policy |

## Source-of-truth matrix

| File or component | Ownership | Authority |
|---|---|---|
| `global/qwen/QWEN.md` | Global Qwen | Universal engineering behavior |
| `global/ao/rules/agentRules.md` | Global AO | Worker and reviewer lifecycle |
| `global/ao/rules/orchestratorRules.md` | Global AO | Coordination and routing lifecycle |
| `global/ao/policies/semanticReviewPublication.md` | Global AO | GitHub semantic-review publication lifecycle |
| `global/qwen/skills/ao-pr-review/references/contract.md` | Global review Skill | Technical review/result contract |
| `global/qwen/skills/ao-pr-review/references/policy.md` | Global review Skill | Effort selection and semantic disposition |
| `global/qwen/skills/ao-pr-review/SKILL.md` | Global review Skill | Qwen execution procedure |
| `templates/project/PROJECT.md` | Project baseline | Product scope, HITL and public-contract template |
| `templates/project/ARCHITECTURE.md` | Project baseline | Architecture and technical-decision template |
| `templates/project/QWEN.md` | Project baseline | Thin repository-local Qwen entry point |
| `templates/project/.qwen/review-rules.md` | Project baseline | Project-only semantic invariants |
| `templates/project/.qwen/review-config.json` | Project baseline | Machine-readable additive review risk metadata |
| `templates/project/.agent-harness.json` | Project baseline | Project lifecycle feature configuration; semantic review enabled by default |
| `templates/project/template-manifest.json` | Project baseline | Required rendering/copy/merge contract for initialization |
| `config/project-harness.schema.json` | Harness configuration | Machine-readable schema for `.agent-harness.json` |
| `templates/verify/generic/scripts/verify` | Verification baseline | Fail-fast rendered implementation of the `scripts/verify` interface |
| `templates/verify/*/profile.json` | Verification profiles | Evidence-driven stack/composite check and CI-setup candidates |
| `config/verification-profile.schema.json` | Harness configuration | Machine-readable verification-profile contract |
| `templates/project/.github/workflows/verify.yml` | Project baseline | CI wrapper invoking `scripts/verify` |

## Target repository structure

```text
AO-QwenCode-Harness/
├── global/
│   ├── ao/
│   │   ├── rules/
│   │   │   ├── agentRules.md
│   │   │   └── orchestratorRules.md
│   │   └── policies/
│   │       └── semanticReviewPublication.md
│   └── qwen/
│       ├── QWEN.md
│       ├── settings.template.json
│       └── skills/
│           └── ao-pr-review/
├── templates/
│   ├── project/
│   ├── verify/
│   └── prompts/
├── lifecycle/
├── config/
├── install/
└── docs/
```

The structure is intentional: host-global deployable assets are separated from project bootstrap templates, lifecycle utilities, host/deployment configuration, installers, and architecture documentation.

## Core ownership decisions

1. `~/.qwen/QWEN.md` governs all Qwen sessions on the host. It contains only universal engineering rules.
2. AO worker and orchestrator rules are host-global and project-neutral.
3. GitHub semantic-review publication is harness behavior and therefore global, not copied into each project.
4. `PROJECT.md` and `ARCHITECTURE.md` remain project-owned because their substance describes product truth.
5. Project `QWEN.md` remains intentionally thin and points Qwen to project contracts rather than duplicating global behavior.
6. `.qwen/review-rules.md` exists only for project-specific semantic invariants that cannot be reliably enforced mechanically.
7. `scripts/verify` is the mandatory stable verification interface for every managed project; its implementation is stack-specific.
8. CI invokes the same `scripts/verify` entry point so local and CI verification do not drift.
9. `ao-pr-review` owns review execution, result validity, effort selection and semantic disposition. AO consumes those results and owns lifecycle actions.
10. Semantic review is enabled by default but may be disabled explicitly for a project. A disabled lifecycle still requires the same exact PR/SHA handoff and deterministic CI qualification; it stops before semantic-review dispatch and creates no semantic-review publication.
11. Pull-request-based development is the harness baseline for implementation changes intended for integration; read-only or no-change tasks do not invent pull requests.
12. Project-specific semantic-review risk metadata is additive only and schema-validated through `.qwen/review-config.json`; repositories cannot remove global protected paths or labels. The review Skill reads policy only from the tracked repository `HEAD`, rejects untracked local policy, and fails closed on malformed configuration before semantic inference.
13. Project lifecycle feature flags belong in `.agent-harness.json`, not in Qwen review-risk configuration. Its schema is host-independent; semantic review defaults to enabled.
14. Project template sources may contain only tokens declared by `template-manifest.json`. Initialization must fail closed until every required token is resolved from inspected facts or explicit human decisions.
15. Verification profiles separate inspection facts from render tokens and declare both machine-readably. Profile candidates are selected only when supported by repository evidence; they cannot silently introduce new quality policy.

## Deduplication rules

A rule may be referenced in multiple places, but it is defined normatively in one place only.

- Verification mechanics are defined by `scripts/verify`, not repeated in review rules or architecture prose.
- Semantic disposition is defined by the review Skill policy, not redefined by AO rules.
- Technical review/result validity is defined by the review Skill contract, not redefined by publication policy.
- Product HITL boundaries are defined by `PROJECT.md`; global rules only require Qwen/AO to respect them.
- Architecture facts are defined by `ARCHITECTURE.md`; review rules may reference them but should not duplicate them.
- Universal Qwen behavior is defined by global `QWEN.md`, not copied into every project.

## Project template rendering contract

Files under `templates/project/` are production template sources. A rendered project baseline is deployable only after every token declared in `template-manifest.json` has been resolved, all copy-as-is files and merge fragments have been applied idempotently, `scripts/verify` has been supplied by the selected verification profile, and template validation passes.

The initializer must never substitute guessed project facts merely to complete rendering. Missing product policy, architecture decisions, security/persistence boundaries, CI setup, or other required values are human-resolution blockers.

`gitignore.harness.fragment` is merged into an existing `.gitignore` rather than replacing repository-owned ignore rules.

## Multi-stack strategy

The harness must remain technology-stack neutral at the global policy level. Stack-specific behavior belongs in project bootstrap output and verification templates.

Verification profiles cover Go, Node/TypeScript, Python, Rust, and Go+Node composition as evidence-driven accelerators. A profile is never automatic policy: inspection must confirm each selected check from repository tooling or explicit project policy. The generic verifier renders the selected preflight and check steps into the single `scripts/verify` authority. When no profile fits, inspection derives that script directly from observed commands and surfaces ambiguous policy for human approval.

## Project onboarding

A read-only first-project inspection must identify languages, package managers, build systems, tests, lint/type checks, CI, public APIs, security and persistence boundaries, generated files, high-risk paths, deployment assumptions, and existing contributor/agent guidance.

It then proposes project-specific contents for the baseline files without inventing unsupported policy. Observed facts and human decisions must remain clearly separated.

## Deployment quality standard

Files committed under `global/`, `templates/`, `lifecycle/`, `config/`, and `install/` are deployment artifacts, not drafts. They must be internally consistent, documented, testable, fail closed where safety or identity is uncertain, avoid hardcoded host/project identity, and be suitable for production deployment at the time they are committed.

Historical checkpoints may document earlier decisions, but active architecture and deployment files are authoritative.

## Decision maintenance

When a decision changes:

1. update the authoritative implementation or policy file;
2. update this architecture record in the same commit;
3. remove or correct obsolete duplicate statements elsewhere;
4. add or update qualification coverage when behavior changes;
5. record migration implications when an installed host or existing project is affected.
