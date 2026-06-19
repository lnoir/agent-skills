# agent-skills

A small, growing collection of agent skills for Claude Code. Consolidating
scattered skills here, starting with the pairing/observer pair.

## Skills

### [agent-pairing](agent-pairing/)
Run paired agents on a coding task: a background **Executor** writes the code
while your session acts as a live **Observer**, auditing every save and feeding
findings back through a shared `.pairing/` file until the task converges clean.
Builds on `agent-observer`. Each role's contract lives in [`roles/`](agent-pairing/roles).

### [agent-observer](agent-observer/)
Watch a workspace and audit edits in real time. A background watcher wakes the
agent only on a **git-confirmed change — never on a timer** — so every wake has
something to review. `fs.watch` for latency, a `git status` poll for the saves it
misses (rename-replace edits). Static-only: it reasons by reading diffs, and never
runs build/test/lint (those pollute the diff and contend with the editor).

## Install

Skills are invoked from `~/.skills/`. Symlink each one (recommended, so it tracks
the repo) — or copy it:

```bash
ln -s "$PWD/agent-pairing"  ~/.skills/agent-pairing
ln -s "$PWD/agent-observer" ~/.skills/agent-observer
```

The watchers run via `bun ~/.skills/<skill>/scripts/watch.js`, so that path must
resolve (requires [bun](https://bun.sh) and a git repo in the workspace being
watched).

## Status

Early — just starting to gather skills into one place. More to follow.
