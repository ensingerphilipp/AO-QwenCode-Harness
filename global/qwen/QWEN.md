# Global Qwen Engineering Rules

These rules apply to all Qwen sessions on this host. Repository instructions may add project-specific requirements and constraints; they do not silently remove these baseline rules.

## Project context

- Before changing a repository, read its available project instructions and contracts, including `QWEN.md`, `PROJECT.md`, `ARCHITECTURE.md`, and `.qwen/review-rules.md` when present.
- Treat repository contracts as authoritative for project goals, architecture, public interfaces, security and persistence boundaries, supported workflows, and human-approval requirements.
- Do not invent missing product policy, architecture decisions, requirements, or approval. Surface material ambiguity for human resolution.

## Scope and change discipline

- Work only within the assigned scope and respect all project-defined human-in-the-loop boundaries.
- Keep fixes and maintenance changes narrow; do not bundle unrelated refactoring or behavior changes.
- Do not introduce net-new product behavior without an approved requirement, issue, objective, or explicit human instruction.
- Preserve declared public contracts unless the project explicitly authorizes changing them.

## Verification

- Use the repository's declared deterministic verification entry point. Harness-managed projects use `bash scripts/verify`.
- Run verification after relevant changes and before handoff.
- Do not weaken, bypass, suppress, exclude, or disable required checks merely to make a change pass.
- Do not modify unrelated product code solely to satisfy verification.
- Fix in-scope underlying defects; report unrelated or pre-existing failures instead of silently repairing or hiding them.
- Do not silently skip required checks or prerequisites. If a required tool or environment prerequisite is unavailable, report the exact blocker.

## Tests

- Bug fixes should include a failing-first regression test when reasonably feasible.
- If deterministic reproduction is not reasonably feasible, document why and provide the strongest deterministic verification available.
- Prefer deterministic, isolated tests over real external services, mutable host state, or timing-dependent behavior when the project provides suitable test seams.
- Test meaningful behavior; do not optimize for an arbitrary coverage number unless the project explicitly defines one.

## Safety and repository integrity

- Do not expose credentials, API keys, tokens, private keys, or other secrets in code, logs, tests, fixtures, comments, or reports.
- Do not change project contracts, verification or CI policy, architecture decisions, security or persistence boundaries, or public interfaces unless explicitly authorized.
- Preserve repository state outside the assigned change. Do not discard, overwrite, or reset unrelated user or agent work.

## Completion

- Before declaring work complete, report the relevant verification result and any unresolved failures, assumptions, or human decisions still required.
- AO-specific worker, reviewer, and orchestrator handoff protocols are defined by AO rules, not here.
