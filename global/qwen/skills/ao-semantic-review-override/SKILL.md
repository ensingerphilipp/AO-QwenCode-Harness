---
name: ao-semantic-review-override
description: Manually publish a successful ao/semantic-review commit status for one exact open PR after all other required deterministic checks pass. Operator escape hatch only.
argument-hint: '<PR-number-or-URL> <reason>'
user-invocable: true
disable-model-invocation: true
---

# ao-semantic-review-override

Human-only escape hatch for deliberately overriding the required GitHub
commit-status context `ao/semantic-review` on one pull request.

This Skill is intentionally hidden from model invocation by Qwen Code's
`disable-model-invocation: true`. It may run only after the operator explicitly
invokes `/ao-semantic-review-override`.

## Invocation

```text
/ao-semantic-review-override <PR-number-or-URL> <reason>
/ao-semantic-review-override help
```

A non-empty reason is mandatory and is included in the GitHub status
description in bounded form for auditability.

## Execution

For a real invocation, use only the session-private argument file supplied by
Qwen Code in the `<skill-args-file>...</skill-args-file>` tag. Do not reconstruct
or alter arguments from conversation text.
Run exactly:

```text
python3 <skill-dir>/scripts/override_status.py --args-file <injected-path>
```

Run it from the current repository worktree. Do not substitute `gh api`, a
handwritten status command, or another helper.

For `help`, if an args file is present use the same command. If no args file is
present, run:

```text
python3 <skill-dir>/scripts/override_status.py help
```

Report the helper's result to the operator. Never merge, approve, comment,
label, edit code, repair findings, dispatch another review, or perform any
other GitHub/AO mutation as part of this Skill.

## Safety boundary

The helper fails closed unless all of these hold on the exact live PR head:

- the PR is open and not draft;
- tracked `.agent-harness.json` enables semantic review;
- at least one other required check exists and every such check passes;
- the PR head is unchanged immediately before publication.

It publishes only `ao/semantic-review=success` on that exact head with the
canonical PR URL as target URL, then verifies the resulting status. It never
changes the semantic-review evidence or disposition and never creates an AO
semantic-review summary comment.
