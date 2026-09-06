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
| `templates/project/.qwen/review-config.json` | Project baseline | Machine-readable review risk metadata |
| `templates/project/scripts/verify` | Project baseline | Deterministic verification entry point |
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
10. Semantic review is enabled by default but may be disabled explicitly for a project.

## Deduplication rules

A rule may be referenced in multiple places, but it is defined normatively in one place only.

- Verification mechanics are defined by `scripts/verify`, not repeated in review rules or architecture prose.
- Semantic disposition is defined by the review Skill policy, not redefined by AO rules.
- Technical review/result validity is defined by the review Skill contract, not redefined by publication policy.
- Product HITL boundaries are defined by `PROJECT.md`; global rules only require Qwen/AO to respect them.
- Architecture facts are defined by `ARCHITECTURE.md`; review rules may reference them but should not duplicate them.
- Universal Qwen behavior is defined by global `QWEN.md`, not copied into every project.

## Multi-stack strategy

The harness must remain technology-stack neutral at the global policy level. Stack-specific behavior belongs in project bootstrap output and verification templates.

Baseline verification templates may cover Go, Node/TypeScript, Python, Rust and composite stacks. When a repository does not fit a standard template, first-project inspection must derive an exact `scripts/verify` implementation from observed project tooling and clearly surface unsupported policy decisions for human approval.

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
