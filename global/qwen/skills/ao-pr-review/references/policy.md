# ao-pr-review policy

## Effort policy (effortPolicyVersion=3)

Effort selection is deterministic, local, fail-closed, and unit-tested. It never uses a model call.

- Explicit `low` always selects native `low`; automatic selection never chooses low.
- Explicit `high` always selects high.
- `auto` selects high for any hard-risk trigger, or when at least two independent soft-risk signals match; otherwise it selects medium.
- Explicit `medium` follows the same promotion rule as auto.

`result.json` records `requestedEffort`, `selectedEffort`, `effortPolicyVersion`, `effortReasons[]`, and project risk-config provenance.

### Global hard-risk paths

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

Schema v1 remains supported for compatibility: `highRiskPaths` and `highRiskLabels` are hard triggers. Schema v2 additionally supports `softRiskPaths`.

```json
{
  "schemaVersion": 2,
  "highRiskPaths": ["package-lock.json"],
  "softRiskPaths": ["src/config/**"],
  "highRiskLabels": ["data-migration"]
}
```

A matching soft project path contributes one independent soft signal. Two independent soft signals select high; one alone remains medium. A path may not appear in both high and soft lists.

The file is optional, additive only, read from the tracked repository `HEAD`, bounded to 64 KiB, and fail-closed on malformed or untrusted content.

### Other hard-risk signals

Any of the following selects high:

1. **High-risk labels**: `security`, `architecture`, `breaking-change`, `release-critical`, `risk:high`, `concurrency`, plus project-configured labels.
2. **Title/body markers**: `risk:high`, `high-risk`, `high risk`, `breaking change`, `breaking-change`, or `security`.
3. **Large change**: `changedFiles >= 15` or `additions + deletions >= 500`.
4. **Strong patch signals**: concurrency primitives; authentication/password/private-key/API-key changes; TLS/cryptography terms; migration/DDL/SQLite/Postgres/MySQL terms.
5. **Incomplete risk evidence**: missing or malformed metadata, incomplete file lists, patch retrieval failure, or an over-bound patch.

### Soft patch signals

Each category contributes at most one soft signal:

- concurrency: channel/chan, race, scheduling, state-machine;
- authentication/permissions: permission, secret, credential, token;
- persistence/schema: schema, database, persistence terms.

A single generic term therefore does not force high. Two independent soft signals do.


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
