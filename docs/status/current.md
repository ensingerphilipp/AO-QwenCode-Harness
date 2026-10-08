# Current Harness Implementation Status

Last updated: 2026-10-08

## Completed

- A — policy ownership and deduplication analysis
- B — global Qwen engineering layer
- C — generalized AO worker and orchestrator rules
- D — global semantic-review publication policy
- E — native review contract consolidated into AO rules
- F — production project baseline templates
- G — deterministic verification profiles
- H — evidence-driven project inspection contract
- I — generalized orchestrator worktree refresh
- Gap closure — harness repository self-verification and CI
- J — host-independent installation and new-project registration
- K — Premiumizearr-Nova migration, installed runtime verification, and accepted tracker-intake smoke qualification
- Operator escape hatch — model-hidden manual `ao/semantic-review` status override packaged and managed as a host-global Skill
- Native review v0.4.0 — persistent Qwen Chat/ACP reviewer backbone using native `/review`; AO-managed reviews are fixed to native high because posting is high-only; the old risk/effort selector and `.qwen/review-config.json` consumption, nested `qwen review run`, Monitor envelopes, synthetic `result.json`, semantic artifact validation, automatic resume, and AO-composed semantic summary comments are removed
- Two-phase native publication — AO preflights a non-posting verdict (`comment.effective=false`), revalidates the exact head after the verdict, then authorizes the same reviewer to publish or discard; native Step 9 cleanup is deferred until successful submission or discard and Qwen owns the internal mechanics of later `post comments` continuation
- Host-global semantic-review admission — deterministic strict FIFO before reviewer creation; queued reviews are model-idle and the active ticket remains held through the AO publish/discard phase and terminal acknowledgement

## Latest qualification

K was accepted complete by the user on 2026-09-07 after the AO 0.12.12 configuration-preservation checks and the tracker-intake smoke issue #85 → PR #86 run. The GitHub evidence covered the documentation-only change, deterministic CI, exact-HEAD semantic review, and successful `ao/semantic-review` publication. The user confirmed that the merge was manual.

PR #105 on 2026-10-08 established the persistent same-session `/review` → later `post comments` mechanism. That run included an intermediate neutral review followed by the final blocking review, so the committed v0.4.0 flow still requires a clean release-qualification run; the harness leaves the native follow-up mechanics to Qwen unless a repeatable integration problem requires a narrower contract.

## Current host deployment

- Current operator baseline is AO **v0.13.2** with Qwen Code **0.24.7**. The harness qualifies required AO/Qwen behavior by CLI capability rather than version strings.
- The current local-inference host has **three available inference slots**, so its operator-owned Qwen runtime environment caps both tool and native-review workflow concurrency at `3` (`QWEN_CODE_MAX_TOOL_CONCURRENCY=3`, `QWEN_CODE_MAX_WORKFLOW_CONCURRENCY=3`) and uses `QWEN_CODE_WORKFLOW_STALL_SECONDS=600`.
- On the inspected `opencode` host, Qwen reports 0.24.7 while `~/.local/bin/ao` still resolves to the prior user-scoped `agent-orchestrator-0.13.1` directory and `ao --version` reports `dev`; that host must be upgraded/repointed before it can be claimed as an independently verified 0.13.2 deployment.
- Worker/reviewer → orchestrator lifecycle communication uses directed `ao send`; persistent reviewers run in Chat/ACP mode and AO uses steered follow-up turns for setup, native `/review`, and publish/discard control.
- `ao-reconcile` and `ao-reboot-recovery` were intentionally removed from the current host and are not baseline harness components.

## Remaining

- Qualify the committed v0.4.0 backbone end-to-end on a fresh exact PR head: one persistent reviewer, one native `/review`, one AO publish authorization, exactly one native GitHub review, terminal acknowledgement, and terminal status.
- L — DEFERRED FUTURE WORK: fresh-host/fresh-project end-to-end qualification

This file is the current progress authority.
