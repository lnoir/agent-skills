---
name: agent-pairing
description: Run paired AI agents on a coding task — one background Executor writes the code while this session acts as a live Observer, auditing every save and feeding findings back through a shared file until the task converges clean.
---

# Agent Pairing

Pair an autonomous **Executor** (a background agent that writes the code) with an
**Observer** (this session, auditing edits in real time). The Observer catches
bugs, compile errors, and integration gaps as they are saved and feeds them back
to the Executor through a shared file, looping until the task is done and clean.

This builds on the [agent-observer](../agent-observer/SKILL.md) skill's event-driven wake
mechanism: a background watcher exits the instant a real change is saved, waking
this session to audit the diff. It **exits only on a confirmed content change, never
on a timer** — no change, no wake — so when you are woken there is always
something to review. Confirmation is a **content fingerprint** of every non-ignored
file (`git ls-files -oc --exclude-standard`, tracked *and* untracked), so it catches
edits to untracked files too — `git status` alone cannot (untracked status is binary
`??`, and untracked directories are collapsed), which matters when the work-in-progress
tree is not yet committed. An edit-then-revert hashes identically and does not wake.
A slow poll backs up `fs.watch` for the saves it misses (in-place rename-replace edits).

## Roles (do not blur them)

Each agent's full contract lives in its own file under [`roles/`](roles/):

| Agent | Plays | Mandate |
|-------|-------|---------|
| **This session** | Observer + Orchestrator | [`roles/observer.md`](roles/observer.md) |
| **Background `Agent`** | Executor | [`roles/executor.md`](roles/executor.md) |

In short: the Observer is **static-only** (list/diff/read/grep — never build,
test, lint, commit, or edit source) and the Executor writes the code and owns all
dynamic verification. Read [`roles/observer.md`](roles/observer.md) now — it is
your mandate for this session. The orchestrator hands the Executor its contract at
kickoff (inlined into the spawn prompt), so it can't be skipped.

## The `.pairing/` channel

The shared bus between the two agents. There is no live message channel into a
running background agent, so these files are how findings flow back.

Each run is isolated under its own directory, so re-runs and parallel work don't
collide and a fresh session can resume an interrupted loop:

```
.pairing/
  run.json                  # active run: id, status, round
  runs/<run-id>/
    task.md                 # the brief
    findings.md             # append-only audit log (IDs scoped to this run)
    last-review             # git stash create snapshot SHA
  task.md       -> runs/<run-id>/task.md       # stable symlinks to the active
  findings.md   -> runs/<run-id>/findings.md   # run, so the Executor's paths and
  last-review   -> runs/<run-id>/last-review   # the role contract never change
```

- `findings.md` entries: an ID, severity, `file:line`, a short description, and a
  status marker `OPEN` or `RESOLVED`. IDs restart per run.
- `last-review` — a `git stash create` snapshot SHA, so each audit diffs only what
  is *new* since the previous review instead of re-reporting the whole tree.
- **One pairing session per working tree.** The watcher is repo-global, so two
  sessions in one tree would audit each other's edits. For parallel runs, use
  separate git worktrees — that isolates both `.pairing/` and the file watching.

## Phase 1 — Kickoff

1. Lay out the run-scoped channel and keep it out of commits:
   ```bash
   RUN_ID=$(date +%Y%m%d-%H%M%S)
   RUN_DIR=.pairing/runs/$RUN_ID
   mkdir -p "$RUN_DIR"
   : > "$RUN_DIR/findings.md"
   git stash create > "$RUN_DIR/last-review"   # may be empty on a clean tree; fine
   ln -sfn "runs/$RUN_ID/task.md"     .pairing/task.md
   ln -sfn "runs/$RUN_ID/findings.md" .pairing/findings.md
   ln -sfn "runs/$RUN_ID/last-review" .pairing/last-review
   printf '{"id":"%s","status":"running","round":0}\n' "$RUN_ID" > .pairing/run.json
   grep -qxF '.pairing/' .gitignore 2>/dev/null || echo '.pairing/' >> .gitignore
   ```
2. Write the task brief to `.pairing/task.md` (the goal, constraints, acceptance
   criteria — enough for the Executor to act without this conversation).
3. Read [`roles/executor.md`](roles/executor.md) and spawn the Executor as a
   **background** `Agent` (`run_in_background: true`), **inlining the role file's
   full contents** into the prompt — don't make the Executor go fetch it; deliver
   the contract directly so it can't be skipped. The prompt is the role file
   verbatim, followed by:

   > Your task brief is in `.pairing/task.md`. Read it and implement it in this
   > workspace now.

   Spawn an Executor that actually **has the tools to verify its own work** (e.g.
   the `general-purpose` agent, which can run the build/tests) — "owns dynamic
   verification" only holds if it can run things. If it reports it couldn't verify,
   fall back to the Observer's read-only confirm carve-out in
   [`roles/observer.md`](roles/observer.md). The role file stays the single
   maintained source of the contract; the orchestrator just hands it over.

4. Arm the watcher as a **background task** (`run_in_background`). The watcher ships
   with this skill at `scripts/watch.js`. Invoke it by **absolute path**: take the
   directory you loaded *this* SKILL.md from and append `/scripts/watch.js`. Do
   *not* `cd` into the skill — the watcher watches its own cwd, which must stay at
   the workspace you are auditing:
   ```bash
   # SKILL_DIR = absolute path of the directory containing this SKILL.md.
   # You already know it — it's where you located this skill (varies by tool:
   # ~/.claude/skills/agent-pairing, an Antigravity skills dir, a repo clone, …).
   bun "$SKILL_DIR/scripts/watch.js"
   ```
   It waits indefinitely and exits only on a real change — it will not wake you on
   a timer.
5. **Stop calling tools** and go idle. Both the watcher and the Executor are now
   background tasks; the next wake comes from whichever finishes first.

## Phase 2 — Wake & branch

A wake is one of two signals. Branch on it.

### (a) Watcher exited with `Detected change:` — a real change was saved
1. Diff only what is new since the last review, and catch new untracked files:
   ```bash
   BASE=$(cat .pairing/last-review)
   git diff ${BASE:-HEAD}
   git status --porcelain          # read any new untracked files in full
   ```
   The watcher only exits on a confirmed content change, so there is normally
   something here. The lone exception is a change reverted in the split second
   between the watcher's check and yours — if the diff is genuinely empty, re-arm
   the watcher (Phase 1 step 4) and go idle without reporting.
2. Read `.pairing/findings.md` first to avoid re-flagging. Reconcile: mark items
   `RESOLVED` if this delta fixes them.
3. Audit the new delta (the [agent-observer](../agent-observer/SKILL.md) lens): compile/syntax
   errors, integration gaps (arg parsed but not passed, route changed but caller
   not updated), logic bugs, off-by-ones, secrets/values that don't add up.
4. Append new findings to `.pairing/findings.md`, then advance the snapshot:
   ```bash
   git stash create > .pairing/last-review
   ```
5. Print a concise report to the user (what changed, new findings, resolved).
6. Re-arm the watcher (Phase 1 step 4) and go idle.

### (b) Executor task-notification — the Executor finished
1. Do a final full audit (`git diff $(cat .pairing/last-review)` + `git status`).
2. Reconcile `findings.md`. If **OPEN** findings remain AND the `round` in
   `.pairing/run.json` is fewer than **3**: bump `round` in `run.json`, re-spawn
   the Executor (background `Agent`, same inlined [`roles/executor.md`](roles/executor.md)
   contract) asking it to resolve the remaining `OPEN` items in
   `.pairing/findings.md`, re-arm the watcher, go idle.

   > Note the async race this guards against: the Executor may finish reporting
   > "no open findings" because it read `findings.md` *before* the Observer wrote
   > the latest one. The convergence round is the backstop — it is load-bearing,
   > not optional.
3. If clean — or the 3-round cap is hit — **stop the watcher** (`TaskStop`), set
   `status` to `done` in `run.json`, write a final summary to the user (what was
   built, findings resolved, any that remain open at the cap), and end the loop.

There is no third "idle" signal — the watcher never wakes you with nothing to
review. Termination comes from the Executor finishing (branch b), not from a clock.

## Resuming an interrupted run

If this session starts fresh on a tree where `.pairing/run.json` already exists
with `status: running`, a previous loop was interrupted (its orchestrator session
ended). Read `run.json` and `.pairing/findings.md`, then:
- **OPEN findings remain** → re-spawn the Executor for them (continue, don't reset,
  the `round` count) and re-arm the watcher.
- **none open but the task looks unfinished** → re-spawn to finish.
- **done** → set `status: done` and stop.

To start an unrelated new task instead, run Phase 1 — a new `RUN_ID` archives the
old run under `.pairing/runs/` and repoints the symlinks; nothing is lost.

## Stopping

The loop ends when the Executor completes and findings converge clean, the
convergence cap is reached, or whoever started the session interrupts it. The
watcher will not end it for you — always `TaskStop` the watcher when you stop.

Because there is no idle timeout, a hung Executor that neither writes nor returns
leaves the loop waiting silently. That is the deliberate trade for "never wake
with nothing to review": a stuck run is ended by you noticing and interrupting,
not by a timer. The Observer never commits — leave git history to the user.
