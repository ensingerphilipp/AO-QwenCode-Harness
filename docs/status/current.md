# Current Harness Implementation Status

Last updated: 2026-10-28

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

- Current observed opencode deployment has AO GUI/AppImage **v0.14.5** with Qwen Code **0.26.0**. The harness qualifies required AO/Qwen behavior by CLI capability rather than version strings.
- As verified 2026-10-13, the local-inference host has **four logical inference slots** (two requests per SGLang estate, two estates), each with a 262,144-token context limit. Its operator-owned Qwen runtime caps tool and native-review workflow concurrency at `4` (`QWEN_CODE_MAX_TOOL_CONCURRENCY=4`, `QWEN_CODE_MAX_WORKFLOW_CONCURRENCY=4`); the documented stall setting is `QWEN_CODE_WORKFLOW_STALL_SECONDS=600`. These settings are host-specific and are not set by the harness.
- On the inspected `opencode` host, the AO AppImage embeds version 0.14.5. The old 0.13.1 user-scoped tree was retired after the harness-managed `~/.local/bin/ao` launcher successfully bound to the 0.13.7 AppImage CLI. `ao --version` still prints `dev` upstream, so binary origin is established from the AppImage source signature and extracted CLI hash, not that string. The CLI launcher checks for AppImage updates on each invocation.
- Worker/reviewer → orchestrator lifecycle communication uses directed `ao send`; persistent reviewers run in Chat/ACP mode and AO uses steered follow-up turns for setup, native `/review`, and publish/discard control.
- `ao-reconcile` and `ao-reboot-recovery` were intentionally removed from the current host and are not baseline harness components.

## Remaining

- Qualify the committed v0.4.0 backbone end-to-end on a fresh exact PR head: one persistent reviewer, one native `/review`, one AO publish authorization, exactly one native GitHub review, terminal acknowledgement, and terminal status.
- L — DEFERRED FUTURE WORK: fresh-host/fresh-project end-to-end qualification

This file is the current progress authority.
