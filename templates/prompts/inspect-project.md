# Project Inspection Task

Inspect the current Git repository to prepare it for the AO + Qwen Code harness. This phase is read-only: do not edit files; do not install dependencies; do not run formatters or builds/tests that may mutate the worktree; do not create commits/branches, push, open PRs, or change repository/host configuration.

Use repository files, read-only Git metadata, and existing CI/configuration as evidence. Do not infer project policy solely from language conventions or personal preference.

## Required inspection

Determine, with file/path evidence where applicable:

- repository identity, remotes, and default branch;
- languages, package managers, toolchains, and declared versions;
- build, format, lint, type-check, test, and packaging commands already defined by the repository;
- CI workflows and required-check intent visible in repository configuration;
- system/component boundaries and primary runtime/data flows;
- public APIs, CLI/configuration contracts, and compatibility expectations;
- security, authentication/authorization, secret-handling, and trust boundaries;
- persistence, migrations/schema, state ownership, and destructive-operation boundaries;
- external services and runtime/deployment dependencies;
- generated/vendor paths and files that should not be edited manually;
- existing contributor, agent, review, architecture, and project-policy guidance;
- project-specific paths/labels whose changes warrant high-effort semantic review.

## Verification proposal

Select zero or more verification profiles only when repository evidence supports them. Profiles are accelerators, not policy. Produce exact preflight and verification commands from observed project tooling; do not add a formatter, linter, type checker, race detector, coverage threshold, build target, or test mode merely because it is conventional for the language.

The final project must expose `bash scripts/verify`, and CI must invoke the same script after explicit CI toolchain/setup steps. Verification must be deterministic, fail-fast, and non-repairing.

If existing verification is incomplete or ambiguous, record the missing policy as a human decision instead of inventing a gate.

## Template values

Populate every token required by `templates/project/template-manifest.json`. Use concise project-specific text suitable for direct rendering into the production project files.

When a token cannot be established from repository evidence without making a policy decision, set that token value to `null` and add a corresponding `decisionsRequired` entry. Do not use `TBD`, guesses, generic filler, or invented policy.

For an observed absence, use a positive statement only when evidence supports it (for example, “No persistent database or migration system is present in the inspected repository”). Do not confuse “not found” with “does not exist” when inspection is incomplete.

## Output contract

Return one JSON object conforming exactly to `config/project-inspection.schema.json`. The JSON is the machine-consumable inspection result; do not wrap it in Markdown fences.

Every asserted fact and every proposed verification command must cite repository evidence through the schema's evidence objects. Evidence paths are repository-relative when they refer to repository files.

`reviewRisk.highRiskPaths` and `reviewRisk.highRiskLabels` are additive project risk metadata for `.qwen/review-config.json`; do not repeat global protected paths solely because the harness already protects them.

`decisionsRequired` contains only genuine human policy/architecture choices that cannot be resolved from repository truth. Each item must identify the affected template tokens.

Do not mark inspection complete when required template values remain `null` without matching decision entries, or when the verification proposal contains no defensible required check.
