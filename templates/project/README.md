# Project Baseline Template Contract

These files are production template sources for repositories managed by the AO + Qwen Code harness. They are not examples and must not be copied with unresolved template tokens.

## Rendering contract

- `template-manifest.json` is the authoritative list of rendered files, copy-as-is files, merge fragments, and required generated files.
- Required tokens use the uppercase double-brace form present in the rendered template files and must be replaced from inspected repository facts or explicit human decisions.
- Initialization must fail if any required token remains unresolved or if a required value cannot be established safely.
- Do not invent project facts or policy to satisfy a token. Escalate unresolved decisions for human approval.
- Copy-as-is files are already valid baseline configuration and may be extended only according to their schemas and ownership rules.
- `gitignore.harness.fragment` is merged idempotently into the target repository's `.gitignore`; it never replaces existing ignore rules.

## Ownership

- `PROJECT.md` owns product scope, HITL boundaries, public contracts, security, persistence, and deployment policy.
- `ARCHITECTURE.md` owns technical structure, flows, dependencies, approved decisions, and architectural risks.
- `QWEN.md` is intentionally thin and points Qwen to the project contracts.
- `.qwen/review-rules.md` contains only project-specific semantic invariants.
- `.qwen/review-config.json` adds project-specific semantic-review risk metadata; it cannot remove global risk rules.
- `.agent-harness.json` controls project-level harness lifecycle features; semantic review defaults to enabled.
- `.github/workflows/verify.yml` delegates mechanical verification to `bash scripts/verify` after project-specific CI setup.
- `scripts/verify` is supplied by the verification profile or generated from inspected project tooling; exact checks are not duplicated here.
