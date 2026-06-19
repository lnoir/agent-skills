---
name: agent-observer
description: Watch the workspace recursively for file changes and audit the edits for bugs, compilation errors, or integration gaps in real time.
---

# Agent Observer

Use this skill to monitor a workspace passively while another agent (or the user) makes edits, auditing the modifications as soon as they are saved. This implements event-driven pair programming to catch bugs, type issues, and integration gaps early.

To run an Observer alongside an autonomous Executor agent that writes the code, see the [agent-pairing](../agent-pairing/SKILL.md) skill, which builds on this one.

## The Mechanism

The observer runs a background process that listens for file system events. Because the agent environment automatically wakes up as soon as a background task completes, we can:
1. Spawn the watch process in the background.
2. End our turn and go idle (no active work between events).
3. Be woken up the instant a file is modified.
4. Run an audit on the diff, report the findings, and restart the watcher.

**The watcher is only a trigger; git state is the source of truth.** It never trusts the event payload — before exiting it runs `git status --porcelain` and **exits only if the tree actually differs from the baseline captured when it armed.** So a dropped, duplicated, or null-filename event is harmless, and a file event that nets no change (edit-then-revert, a touch, a gitignored write) does *not* wake you. The watcher also **never exits on a timer** — no change, no wake — so whenever you are woken there is something to review. It debounces (~400ms) so it checks git once the save settles, not mid-write.

`fs.watch` is known to **miss in-place rename-replace saves** (the Edit tool and many editors write-temp-then-rename). To cover that, the watcher also runs a slow `git status` poll (~25s) as a safety net through the same git-confirmed exit. Event-driven for latency, polled for completeness — a missed save costs at most one poll interval.

## Static-only discipline

The observer **never runs build, test, or lint commands.** Those write artifacts (`target/`, `dist/`, `__pycache__`, `.eslintcache`, coverage files) that corrupt the `git diff` the audit depends on AND trip the watcher into self-wake loops — and they contend with whoever is doing the editing. Analysis stays static: list, diff, read, grep. If a class of error needs a compiler to confirm (e.g. a cross-file type mismatch), **flag it as a suspicion** ("looks like a type mismatch at `foo.ts:42` — verify") rather than invoking the compiler. The observer also never commits or resets — leave git history alone.

The one carve-out: a *single, read-only, artifact-free* command to confirm one specific fix (e.g. `node cli.js /dev/null`) is acceptable when nothing else is editing. A build, a test suite, or anything that writes `target/`, caches, or coverage is not.

## Workflow

### Phase 1: Start/Resume Watching
On first start, snapshot the current tree so the first audit has a baseline to diff against:
```bash
git stash create > /tmp/observer-last-review   # may print nothing on a clean tree; that's fine
```
Then run the bundled watcher as a **background task** (`run_in_background`). The watcher ships with this skill at `scripts/watch.js`. Invoke it by **absolute path**: take the directory you loaded *this* SKILL.md from and append `/scripts/watch.js`. Do *not* `cd` into the skill — the watcher watches its own cwd, which must stay at the workspace you are auditing:
```bash
# SKILL_DIR = absolute path of the directory containing this SKILL.md.
# You already know it — it's where you located this skill (varies by tool).
bun "$SKILL_DIR/scripts/watch.js"
```
The watcher waits indefinitely and exits only on a real change; it will not wake you on a timer.

After launching it, **stop calling tools** to let the agent go idle.

### Phase 2: Wakeup & Code Audit
The watcher exits only with `Detected change:` (a git-confirmed change), so when you wake, perform the audit:

1. **Diff only what is new since the last review, and catch new files**:
   ```bash
   BASE=$(cat /tmp/observer-last-review 2>/dev/null)
   git diff ${BASE:-HEAD}
   git status --porcelain        # read any new untracked files in full — git diff won't show them
   ```
   The watcher already confirmed a change, so there is normally something here; if the diff is genuinely empty (a change reverted in the split second before you looked), just restart the watcher (Phase 1) and go idle without reporting.
2. **Review the code**:
   * Inspect the exact edits.
   * Look for compilation/syntax errors (e.g. missing struct/class fields, invalid imports, mismatched types).
   * Look for integration gaps (e.g. CLI argument parsed but not passed, API route modified but server-side caller not updated).
   * Inspect logic choices (e.g. returning secrets that weren't generated, off-by-one errors).
3. **Advance the snapshot** so the next wake diffs only the next delta:
   ```bash
   git stash create > /tmp/observer-last-review
   ```
4. **Report to the user**:
   * Output a concise markdown report detailing what changed and any identified bugs, warnings, or recommendations.
5. **Loop**:
   * Restart the watcher process using Phase 1 (skip the baseline snapshot — step 3 already advanced it).

## Stopping

The watcher never stops itself — it waits for the next real change. The loop ends only when whoever started the watch interrupts it (or stops the background task).
