---
name: agent-pairing
description: Run paired AI agents on a coding task — one background Executor writes the code while this session acts as a live Observer, performing a strict audit on every save and feeding findings back through a shared file until the static audit comes back clean.
---

# Agent Pairing

Pair an autonomous **Executor** (a background agent that writes the code) with an
**Observer** (this session, auditing edits in real time). The Observer catches
bugs, compile errors, and integration gaps as they are saved and feeds them back
to the Executor through a shared file, looping until the static audit is clean.

This builds on the [agent-observer](../agent-observer/SKILL.md) skill's event-driven wake
mechanism: a background watcher exits the instant a real change is saved, waking
this session to audit the diff. It **exits only on a confirmed content change, never
on a timer** — no change, no wake. Confirmation is a **content fingerprint** of every
non-ignored file (`git ls-files -oc --exclude-standard`, tracked *and* untracked),
so it catches edits to untracked files too. An edit-then-revert hashes identically
and does not wake. A slow poll backs up `fs.watch` for the saves it misses.

## Fit check — before anything else

Run this gate mentally before Phase 1. If any item fails, say so and fall back to
working normally instead of running a loop that cannot fire:

1. **A local git worktree the Executor writes to.** MCP-only, cloud-side, or
   remote-build tasks produce no local saves — the watcher can never wake and the
   loop silently degrades to one end-of-run read. Don't run it.
2. **The change is big enough to pair on.** For a one-file, few-line change the
   ceremony costs more than it catches — just do it and review inline.
3. **The work surface is visible to git.** If the task's target paths are
   gitignored, the watcher and diffs cannot see them (`pair.js init` warns when
   you pass targets). Fall back to explicit end-of-run review.
4. **One pairing session per working tree.** The watcher is repo-global; two
   sessions in one tree audit each other's edits, and a parallel actor switching
   branches under the run corrupts it. For parallel runs use separate git
   worktrees. `pair.js init` refuses to double-arm over a live run.

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

**Mixed-role work is unsupported.** Don't plan "the Observer implements slice X" —
its own saves trip its own watcher into self-wake loops. If the user insists on
the Observer doing a batch of edits: stop the watcher first (`TaskStop`), make the
batch, run `pair.js audit-begin`/`audit-end` to fold it into the baseline, re-arm.

## The lifecycle CLI

All setup and round bookkeeping goes through **`scripts/pair.js`** — one simple,
non-compound invocation per protocol step, identical across harnesses and
sessions. Invoke it by **absolute path** (`SKILL_DIR` = the directory you loaded
this SKILL.md from) with cwd at the workspace being audited:

```bash
bun "$SKILL_DIR/scripts/pair.js" <command>
```

| Command | Who | Does |
|---------|-----|------|
| `init [--force] [target...]` | Observer | kickoff: preconditions, run dir, `run.json`, local git exclude |
| `status` | Observer | dump `run.json`, watcher liveness, findings tally |
| `audit-begin [--full]` | Observer | snapshot first, then print the delta to audit |
| `audit-end` | Observer | advance `last-review` — only after findings are recorded |
| `round` | Observer | bump the round; exits 2 at the 3-round cap |
| `finish` | Observer | mark done, release the Executor, stop the watcher |
| `executor-ready` | Executor | signal ready_to_finish (wakes the Observer) |
| `executor-wait [--timeout s]` | Executor | poll for the verdict: exit 0 done, 3 new findings, 4 timeout |

Tip: allowlisting `bun */agent-pairing/scripts/*.js *` once removes per-step
permission prompts entirely.

## The `.pairing/` channel

The shared bus between the two agents. There is no live message channel into a
running background agent, so these files are how findings flow back.

```
.pairing/
  run.json                  # SINGLE SOURCE OF TRUTH: id, status, round, paths, handshake
  runs/<run-id>/
    task.md                 # the brief
    findings.md             # append-only audit log (IDs scoped to this run)
    last-review             # git stash create snapshot SHA
```

- **All paths come from `run.json`** (`paths.task`, `paths.findings`,
  `paths.last_review`). Never guess a run directory from a timestamp — clocks
  differ between the terminal and the harness — and there are **no symlinks**;
  file tools fight them (writes through them fail or clobber the link).
- `findings.md` entries: an ID, severity, `file:line`, a short description, and a
  status marker `OPEN` or `RESOLVED`. IDs restart per run.
- `.pairing/` is kept out of commits via **`.git/info/exclude`** (local, never
  staged) — `pair.js init` handles it. Never add it to the tracked `.gitignore`;
  that hunk rides into the next commit. If an old run already added a
  `.pairing/` line to a tracked `.gitignore`, leave it alone (removing it would
  dirty the tree mid-task); just don't add new ones.
- **Harness note:** `.pairing/` files are plain workspace files, not managed
  artifacts. Write them with the basic file-write tool — no artifact metadata,
  no artifact/brain directory routing. If your harness rejects a write as "not a
  valid artifact path", retry as a plain file write with no metadata.

## Phase 1 — Kickoff

1. `bun "$SKILL_DIR/scripts/pair.js" init` (optionally pass the task's target
   paths so it can warn if they are gitignored). It prints `run.json`; note
   `paths.task` and `paths.findings`.
2. Write the task brief to the `paths.task` file (the goal, constraints,
   acceptance criteria — enough for the Executor to act without this
   conversation).
3. Read [`roles/executor.md`](roles/executor.md) and spawn the Executor as a
   **named background `Agent`** (`run_in_background: true`, `name:
   "executor-<run-id>"` — named so `SendMessage` can reach it later), **inlining
   the role file's full contents** into the prompt, followed by:

   > Read `.pairing/run.json`; your task brief is at its `paths.task`. The
   > pairing CLI is at `<absolute path to scripts/pair.js>`. Implement the brief
   > in this workspace now.

   Spawn an Executor that actually **has the tools to verify its own work** (e.g.
   the `general-purpose` agent, which can run the build/tests). If it reports it
   couldn't verify, fall back to the Observer's read-only confirm carve-out in
   [`roles/observer.md`](roles/observer.md).
4. Arm the watcher as a **background task** (`run_in_background`). Do *not* `cd`
   into the skill — the watcher watches its own cwd, which must stay at the
   workspace:
   ```bash
   bun "$SKILL_DIR/scripts/watch.js"
   ```
   It records its PID into `run.json` (so `init`/`finish` can reap it), waits
   indefinitely, and exits only on a real change — or self-reaps after
   `OBSERVER_MAX_LIFETIME_MS` (default 4 h) with a distinct message.
5. **Stop calling tools** and go idle. Parking means exactly this: end the turn
   and wait for the background task notification (watcher exit or Executor
   finish). Do not use `ScheduleWakeup` or any timer — the next wake comes from
   whichever background task finishes first.

## Phase 2 — Wake & branch

A wake is one of three signals. Branch on the watcher's exit message / the
notification type.

### (a) Watcher exited `Detected change: <file/poll reason>` — a save landed
1. `pair.js audit-begin` — it snapshots the tree **first**, then prints the delta
   since the last review plus every untracked file. The snapshot-first ordering
   is what closes the race where edits made *during* your audit would fold into
   the baseline unaudited: anything saved after `audit-begin` appears in the
   next delta. If the delta is empty (edit-then-revert), re-arm and go idle.
2. Read the findings file first to avoid re-flagging. Reconcile: mark items
   `RESOLVED` if this delta fixes them.
3. Audit the delta (the [agent-observer](../agent-observer/SKILL.md) lens):
   compile/syntax errors, integration gaps, logic bugs, off-by-ones,
   secrets/values that don't add up. Also read any file `audit-begin` lists as
   changed-but-untracked in full.
4. Append new findings to `paths.findings`, then `pair.js audit-end`. Never
   advance the baseline any other way — the ordering is what keeps every save
   audited.
5. Print a concise report to the user (what changed, new findings, resolved).
6. Re-arm the watcher (Phase 1 step 4) and go idle.

### (b) Watcher exited `Detected change: executor signalled ready_to_finish`
The Executor believes it is done and is now polling `executor-wait` for your
verdict (bounded — default 150 s — so act promptly):
1. Run the **mandatory whole-run pass**: `pair.js audit-begin --full` diffs from
   the base of the run, not the last delta. This closes the per-delta coverage
   hole — a bug introduced and then buried under later edits within one snapshot
   window never appeared in any single delta.
2. If findings: append them (`OPEN`) and `pair.js audit-end`. The Executor's
   poll sees them, exits code 3, resolves them, and signals ready again. Re-arm
   the watcher and go idle.
3. If clean: `pair.js audit-end`, then `pair.js finish` — this releases the
   Executor (its poll sees `status: done` and exits 0) and stops the watcher.
   Report per **Finishing** below.

### (c) Executor task-notification — the Executor returned without the handshake
(Timeout on `executor-wait`, a harness that killed it, or an Executor that
skipped the protocol.)
1. Run the whole-run pass (`pair.js audit-begin --full`), reconcile findings,
   `pair.js audit-end`.
2. If **OPEN** findings remain: `pair.js round` (it exits 2 at the 3-round cap —
   then finish with the open items noted). Otherwise re-spawn the Executor
   (named background `Agent`, same inlined contract) with the printed respawn
   brief, re-arm the watcher, go idle. This round backstop also covers the old
   async race where the Executor read the findings file before your last append.
3. If clean — or at the cap — `pair.js finish` and report per **Finishing**.

## Finishing — say what the loop did *not* verify

The terminal state is **"static audit clean — runtime unverified"**, never
"converged clean". A static audit means a model read the code and it looked
right; it is not evidence the code works. The final summary MUST list what was
out of reach, per the checklist in [`roles/observer.md`](roles/observer.md):
timing/concurrency behaviour, CI-only outcomes, generated artifacts needing
regeneration, DB-/service-backed tests. Predictable blind spots, stated up
front, beat discovering them in CI.

## Follow-up fixes after done

For a small correction after `finish`, do **not** have the Observer edit source
and do not restart Phase 1. Run a follow-up round in the same run dir:
`pair.js round` won't work on a done run — instead re-open by setting `status`
back to `running` in `run.json`, `pair.js round`, then SendMessage the named
Executor (or re-spawn) with just the fix brief, and re-arm the watcher.

## Resuming an interrupted run

If this session starts fresh on a tree where `run.json` exists with
`status: running`, a previous loop was interrupted. `pair.js status`, read the
findings file, then:
- **OPEN findings remain** → re-spawn the Executor for them (continue, don't
  reset, the `round` count) and re-arm the watcher.
- **none open but the task looks unfinished** → re-spawn to finish.
- **actually complete** → `pair.js finish`.

To start an unrelated new task, `pair.js init --force` archives the old run
under `.pairing/runs/` and reaps its watcher; nothing is lost.

## Stopping

The loop ends via `pair.js finish` (audit clean or the cap), or when whoever
started the session interrupts it. `finish` reaps the watcher by PID; if you
stop any other way, `TaskStop` the watcher yourself. A hung Executor that
neither writes nor returns leaves the loop waiting silently — that is the
deliberate trade for "never wake with nothing to review"; the watcher's
max-lifetime self-reap is the outer bound. The Observer never commits — leave
git history to the user. During any recovery involving branch operations, clear
untracked `.pairing/` state first — leftover files block `git checkout`.
