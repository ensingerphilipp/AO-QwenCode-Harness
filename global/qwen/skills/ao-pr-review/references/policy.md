# ao-pr-review policy

## Effort policy (effortPolicyVersion=2)

Effort selection is deterministic, local, fail-closed, and unit-tested. It never uses a model call. `auto` selects `high` when any high-risk rule matches and `medium` otherwise. Explicit `high` always remains high. Explicit `medium` is promoted to high whenever a high-risk rule matches.

`result.json` records `requestedEffort`, `selectedEffort`, `effortPolicyVersion`, `effortReasons[]`, and project risk-config provenance.

### Global high-risk paths

These harness contract surfaces always select high when changed:

```text
PROJECT.md
ARCHITECTURE.md
QWEN.md
.qwen/review-rules.md
.qwen/review-config.json
scripts/verify
.github/workflows/**
```

These paths are project-neutral and cannot be removed or downgraded by repository configuration.

### Project risk extension

A repository may add high-risk paths and labels in `.qwen/review-config.json`:

```json
{
  "schemaVersion": 1,
  "highRiskPaths": ["src/security/**", "package-lock.json"],
  "highRiskLabels": ["data-migration"]
}
```

The file is optional. When absent from the trusted repository `HEAD`, only the global rules apply. When present, it is read from the tracked `HEAD` blob rather than mutable local/untracked content. An untracked local file at the same path is rejected. The file is additive only and must satisfy all of these requirements:

- tracked regular UTF-8 JSON file in repository `HEAD`, not a symlink;
- at most 64 KiB;
- root is an object with exactly the supported keys;
- `schemaVersion` is exactly integer `1`;
- `highRiskPaths` and `highRiskLabels` are arrays of non-empty strings;
- at most 128 entries per array and 256 characters per entry;
- path patterns are repository-relative POSIX patterns: no absolute paths, backslashes, or `..` segments;
- entries are unique; labels are normalized and must also be unique case-insensitively;
- control characters are rejected;
- malformed, unsupported, unreadable, or ambiguous configuration is `review_error` before Qwen inference.

Project patterns support exact paths, `*` within one path segment, and a trailing `/**` for recursive descendants. Project rules extend but never replace the global rules.

### Other high-risk signals

Any of the following also selects high:

1. **High-risk labels**: `security`, `architecture`, `breaking-change`, `release-critical`, `risk:high`, `concurrency`, plus project-configured labels.
2. **Title/body markers**: case-insensitive `risk:high`, `high-risk`, `high risk`, `breaking change`, `breaking-change`, or `security`.
3. **Large change**: `changedFiles >= 15` or `additions + deletions >= 500`.
4. **Bounded patch signals**: concurrency/synchronization, authentication/permissions/secrets, TLS/cryptography, or persistence/schema/database terms.
5. **Incomplete risk evidence**: missing/malformed size, file, or label metadata; a changed-file count exceeding the valid returned path count; patch retrieval failure; or a patch exceeding the 512 KiB inspection bound.

Incomplete evidence selects high rather than guessing medium. There is no special assumption about a GitHub page size or a particular technology stack.

## Disposition policy

Allowed dispositions are `pass`, `blocked`, `needs_human`, `stale`, and `review_error`. Apply them in this order after retrieving the post-review head:

1. Post-review head differs from expected head → `stale`; ignore the old verdict.
2. Native run failure/timeout/incompleteness, malformed or contract-invalid artifacts, unprovable post-review head, or changed/unreadable local repository identity → `review_error`.
3. Valid native exit `3` or valid final event `REQUEST_CHANGES` → `blocked`.
4. Any unresolved `Critical` finding with `high` confidence → `blocked`.
5. Any unresolved `Critical` finding with `low` confidence → `needs_human`.
6. `baseEvent == REQUEST_CHANGES` with a downgraded non-blocking final event and no prior blocker → `needs_human`.
7. Valid `APPROVE`, or acceptable `COMMENT` with no unresolved `Critical`, → `pass`.
8. `Suggestion` and `Nice to have` findings alone → `pass`, preserving every finding.
9. Genuine ambiguity → `needs_human`.

A finding is unresolved when it has no outcome or its outcome is `skipped`. `fixed` and `no_change_needed` are preserved but are not unresolved blockers. The helper never guesses `pass`.

Direct helper exit codes:

```text
0 = pass or help
2 = usage error
3 = review_error
4 = stale
5 = blocked
6 = needs_human
```
