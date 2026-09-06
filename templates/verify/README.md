# Verification Profiles

Every harness-managed repository exposes exactly one deterministic entry point:

```text
bash scripts/verify
```

The rendered script is the sole authority for mechanical verification. CI invokes the same script.

## Production rules

- `scripts/verify` is fail-fast and returns nonzero on any required check failure.
- Verification never edits source, installs project dependencies, changes configuration, suppresses failures, or performs repair.
- Toolchain/dependency setup belongs to documented developer/bootstrap steps and CI setup, not hidden inside verification.
- Exact checks are selected from inspected repository evidence and explicit project policy. A profile never invents a check merely because a language is present.
- Required tools are checked explicitly in preflight; missing prerequisites fail with a clear error.
- If no profile accurately represents the repository, bootstrap renders the generic template from observed project commands and requires human resolution for ambiguous policy.

## Profiles

`generic/` is the production script template. `go/`, `node/`, `python/`, `rust/`, and `go-node/` contain evidence-driven profile specifications used during project inspection. Profile candidates are not automatically mandatory; the initializer selects only checks supported by repository facts or approved policy.
