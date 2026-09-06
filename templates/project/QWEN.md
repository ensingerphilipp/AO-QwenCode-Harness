# Project Qwen Instructions

Before making repository changes, read and follow:

- `PROJECT.md` for product scope, contracts, and human-approval boundaries;
- `ARCHITECTURE.md` for system structure and approved technical decisions;
- `.qwen/review-rules.md` for project-specific semantic invariants when present.

Global Qwen rules remain applicable.

The deterministic verification entry point is:

```text
bash scripts/verify
```

Do not duplicate project policy here. Project-specific product policy belongs in `PROJECT.md`; architecture facts belong in `ARCHITECTURE.md`; semantic review invariants belong in `.qwen/review-rules.md`.

If a required project decision is absent or ambiguous, surface it for human resolution rather than inventing policy.
