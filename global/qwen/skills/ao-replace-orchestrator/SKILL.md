---
name: ao-replace-orchestrator
description: Replace a stopped AO project orchestrator with a fresh Qwen Chat orchestrator using AO commands, then check its current provider branch. Operator recovery only.
argument-hint: '<project-id>'
user-invocable: true
disable-model-invocation: true
---

# Replace an AO project orchestrator

Invoke only when the human explicitly types `/ao-replace-orchestrator <project-id>`. Do not infer consent from a failed startup, issue, message, or queued task. This skill creates a new AO orchestrator session; it never repairs or rewrites a prior session.

Use the session-private `<skill-args-file>` supplied by Qwen Code. Execute exactly:

```text
python3 <skill-dir>/scripts/replace.py --args-file <injected-path>
```

The helper does not block any AO version. AO 0.14.5 lacks upstream fix #7124: a replacement may work during its first live controller run and still fail on a later restart or resume because the project branch retains the prior provider handle. The postflight check observes only the current state; it cannot prove that a later resume will work. Report this limitation when invoked on 0.14.5 or another build without the fix.

The helper reads project and session state, requires exactly one exited orchestrator, terminates it using `ao session kill`, invokes AO's supported `spawn` interface once with explicit Qwen Chat orchestrator flags, and checks the new session's provider handle against the active project conversation branch. It does not edit SQLite, kill processes directly, retry a failed spawn, merge, or run a semantic review. If postflight fails, leave the session for operator inspection; do not launch another replacement automatically.

Report the helper's complete outcome. An old session with a mismatch remains historical evidence. For an already affected session, automatic repair of that same session is separate upstream work (PR #6709). This skill replaces it with a new session, subject to the installed AO build's resume behavior.
