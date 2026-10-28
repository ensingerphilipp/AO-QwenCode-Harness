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

Installed versions on `opencode`, verified on **2026-10-28**:

| Component | Installed version | Verification source |
|---|---|---|
| AO GUI/AppImage | **0.14.5** | `resources/app.asar` → `package.json` inside `~/.ao/agent-orchestrator.AppImage` |
| AO CLI | Bundled with **AO 0.14.5**; reports `ao version dev` | SHA-256 of AppImage member `resources/daemon/ao` equals the active CLI cache; see provenance below |
| Qwen Code | **0.26.0** | `qwen --version` and the installed package/bundle manifests under `~/.local/lib/qwen-code/` |
| Qwen bundled Node.js | **22.23.3** | `~/.local/lib/qwen-code/node/bin/node --version` |
| Python | **3.14.4** | `python3 --version` |
| Git | **2.53.0** | `git --version` |
| GitHub CLI | **2.103.0** | `gh --version` |
| `ao-semantic-review-override` Skill | **0.1.0** | Installed `VERSION` equals tracked `global/qwen/skills/ao-semantic-review-override/VERSION` |

These are observed host versions, not minimum supported versions or a claim of end-to-end release qualification. The harness qualifies required AO/Qwen behavior by CLI capability rather than version strings. References to native-review **v0.4.0** in this record and migration guidance identify the harness architecture generation; there is no separately installed `ao-pr-review` Skill or standalone review package in the current managed deployment.

- The version audit passed `bash install/verify-install.sh --project-id premiumizearr-nova`; all 13 managed host files matched authoritative harness `main` at `cefe9733e7af8a11d458caba5e1298a6647d762a` byte-for-byte. The install manifest is a historical installation receipt: its `commandVersions` and `sourceCommit` describe the installation event, not necessarily current software after operator updates.
- As verified 2026-10-13, the local-inference host has **four logical inference slots** (two requests per SGLang estate, two estates), each with a 262,144-token context limit. Its operator-owned Qwen runtime caps tool and native-review workflow concurrency at `4` (`QWEN_CODE_MAX_TOOL_CONCURRENCY=4`, `QWEN_CODE_MAX_WORKFLOW_CONCURRENCY=4`); the documented stall setting is `QWEN_CODE_WORKFLOW_STALL_SECONDS=600`. These settings are host-specific and are not set by the harness.
- AO CLI provenance: the installed **0.14.5** AppImage's `resources/daemon/ao` and `~/.local/state/ao-qwen-code-harness/ao-cli/ao` both have SHA-256 `9ead981b3ed27f7c4251ac1c78e7d46e703ef0857c9892520a99adfd8b55ddf0`. `~/.local/bin/ao` is the managed launcher; it checks for AppImage updates on each invocation. The historical 0.13.1 user-scoped tree was retired after the launcher originally bound to the then-installed 0.13.7 AppImage. The CLI's `dev` string is not a semantic release version.
- Worker/reviewer → orchestrator lifecycle communication uses directed `ao send`; persistent reviewers run in Chat/ACP mode and AO uses steered follow-up turns for setup, native `/review`, and publish/discard control.
- `ao-reconcile` and `ao-reboot-recovery` were intentionally removed from the current host and are not baseline harness components.

## Remaining

- Qualify the committed v0.4.0 backbone end-to-end on a fresh exact PR head: one persistent reviewer, one native `/review`, one AO publish authorization, exactly one native GitHub review, terminal acknowledgement, and terminal status.
- L — DEFERRED FUTURE WORK: fresh-host/fresh-project end-to-end qualification

This file is the current progress authority.
