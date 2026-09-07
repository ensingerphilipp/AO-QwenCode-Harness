# ao-semantic-review-override — v0.1.0

Human-operated escape hatch for marking the exact required GitHub commit-status
context `ao/semantic-review` successful on one pull-request head.

## Trust boundary

The Skill is directly user-invocable but carries:

```yaml
user-invocable: true
disable-model-invocation: true
```

Qwen Code therefore exposes it to explicit slash-command invocation while
excluding it from model Skill discovery/invocation. This is a Skill-level
boundary, not a credential boundary: a model with unrestricted shell access
and independently usable GitHub credentials could still reconstruct a raw
GitHub API request outside this Skill.

## Invocation

```text
/ao-semantic-review-override <PR-number-or-URL> <reason>
```

The reason is mandatory and is retained in bounded form in the commit-status
description.
## Preconditions

The deterministic helper refuses publication unless:

- the PR belongs to the current repository, is open, and is not draft;
- the exact PR head has tracked `.agent-harness.json` with semantic review
  explicitly enabled;
- at least one other required check exists and all such checks pass;
- the PR head is unchanged immediately before status publication.

It publishes only `ao/semantic-review=success` on that exact SHA, uses the
canonical PR URL as target URL, and verifies the resulting status. It does not
change review evidence/disposition, publish the AO summary comment, or perform
any merge/review/code/AO lifecycle mutation.

## Installation

This is a host-global harness asset. Do not copy it manually in production.
Install from the harness repository using:

```bash
bash install/install-host.sh --dry-run
bash install/install-host.sh
bash install/verify-install.sh
```

The manifest-controlled installer deploys the complete tree to
`~/.qwen/skills/ao-semantic-review-override/` and preserves executable mode on
the helper.
