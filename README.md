# agent-skills

A collection of agent skills for Claude Code (and other skill-aware agent tools).

## Skills

### [pr-review](pr-review/)
Review a pull request with a fan-out/verify pipeline: check the PR head out into
an isolated, detached worktree pinned to one commit, fan out specialist subagents
(correctness, security, quality) that return structured findings, verify every
finding against the real code, then draft self-contained comments for approval
before posting a single review.

### [agent-pairing](agent-pairing/)
Run paired agents on a coding task: a background **Executor** writes the code
while your session acts as a live **Observer**, auditing every save and feeding
findings back through a shared `.pairing/` file until the task converges clean.
Builds on `agent-observer`; each role's contract lives in [`roles/`](agent-pairing/roles).

### [adversarial-pr-loop](adversarial-pr-loop/)
Operator-invoked loop for critical work, and for sessions with no local
worktree where agent-pairing can't run: implement with subagents, commit locally, then run a **fresh**
adversarial reviewer each round — findings verified by the orchestrator before
any fix — until a round comes back clean, and only then raise the PR.

### [agent-observer](agent-observer/)
Watch a workspace and audit edits in real time. A background watcher wakes the
agent only on a git-confirmed change — never on a timer — so every wake has
something to review. Static-only: it reads diffs and never runs build/test/lint.

### [collaboration-retro](collaboration-retro/)
Mine local agent transcripts through correction, repetition, and improvement
lenses into a weekly digest. Applying memories, rules, skills, or scripts from
it is a separate manual flow requiring explicit per-proposal approval.

### [skill-review](skill-review/)
Audit an untrusted skill, plugin, or agent-tool package for malicious code and
prompt injection before installing or trusting it.

### [strange-mode](strange-mode/)
Explore multiple parallel implementation variations in git worktrees or sandbox
playgrounds to compare architectural approaches.

## Install

Install each skill wherever your agent tool discovers skills. For Claude Code,
symlink into `~/.claude/skills/` (or `~/.skills/`) so it tracks the repo:

```bash
ln -s "$PWD/<skill-name>" ~/.claude/skills/<skill-name>
```

Skills resolve their own scripts relative to the directory they load from, so
they work wherever they live. The pairing/observer skills require
[bun](https://bun.sh) and a git repo in the watched workspace.
