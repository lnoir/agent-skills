---
name: adversarial-pr-loop
description: Implement tickets with subagents, then run fresh adversarial reviewers until a round comes back clean, then raise the PR. Operator-invoked only — run this loop when explicitly asked to (e.g. /adversarial-pr-loop, or "use the adversarial PR loop"); never adopt it on your own judgement. Suited to cloud/remote sessions where the local agent-pairing flow can't run, and to critical work generally.
---

# Adversarial PR loop

One orchestrator (you) drives each ticket through:

```
implement (subagent) → commit locally
  → adversarial review (fresh subagent)
  → orchestrator verifies findings
  → fix verified findings (back to implementer)
  → repeat review until a round has nothing worth fixing
  → push → PR
```

No PR is opened until a review round is clean. Do push the branch as a backup
after each implementer hand-back (a branch push, no PR): in a cloud container
local commits can vanish when the container is replaced. While no PR exists,
the backup branch is yours — amending a commit and force-pushing it is fine.
If the environment's permission rules block force-push (some auto-modes treat
it as destructive), don't fight it: push a new commit instead. A plain
fast-forward push costs nothing more than an extra commit.

## 1. Plan the tickets

- Create a parent ticket and one child per piece of work, in whatever tracker
  this session has (`bst`, Linear, GitHub issues). If no tracker is reachable,
  keep the same structure in a `PLAN.md` at the repo root and say so in the PR.
- Put enough detail in each child that an agent can do it from the ticket
  alone: diagnosed breakages, file paths, a "Done when" section.
- Add blocking relations, and run in parallel only the tickets that are truly
  independent. Say which ones are sequential and why.
- A library bug found along the way gets its own ticket and its own loop in the
  library repo, with a minimal repro attached. Review the proposed fix
  adversarially *before* implementing it; for bug fixes, have the implementer
  work red/green (failing test first).

## 2. Launch implementers

One subagent per parallel ticket, on the strongest model available. The prompt
includes:

- Its own checkout and branch. For parallel agents, use a separate worktree per
  agent. If the repo uses relative path deps (sibling-repo references), make
  the worktree a **sibling** of the main checkout so the paths still resolve.
- Hard rules:
  - Commit locally only: no push, no PR, no `--no-verify`.
  - Use the repo's commit conventions and trailers.
  - Don't modify sibling repos.
- Process safety when agents share a machine:
  - Never kill processes by name (`pkill`/`killall`). Only kill PIDs you started.
  - Prefer OS-assigned ports over fixed ones.
- Environment gaps: install missing tools with the system package manager
  instead of writing stand-ins, and add them to the environment's setup script.
- If parallel branches need the same small fix, have both make it identically,
  as its own commit, so the merge is clean. Tell each agent to stay out of the
  other's files.
- The report must contain: the commits, per-suite test counts, judgement calls,
  and what was left undone.

## 3. Adversarial review

**Use a fresh reviewer every round.** Don't reuse the implementer or the
previous round's reviewer. Fixes introduce new bugs: in practice, a round-2 fix
has produced a round-3 blocker.

The reviewer prompt (template: [references/reviewer-prompt.md](references/reviewer-prompt.md)):

- It is read-only **with respect to the branch**: no edits, commits or pushes.
  Running things is expected — it may clone, install, and execute test suites
  in its own scratch area.
- It reviews exactly what the PR will contain (`git diff <base>...HEAD`). After
  a fix round, it focuses on the new commits, then skims the whole diff.
- **Claims to check:** what the branch says it does, so the reviewer verifies
  claims instead of trusting them.
- **Previous findings:** a list of them, to confirm they are actually fixed and
  that the fixes caused no regressions.
- **An explicit out-of-scope list:** pre-existing issues already ticketed, and
  settled decisions. Without it, every round re-raises the same things.
- **Targeted hunts**, for example:
  - a fresh-clone run of install plus the full test suite — high value: the
    implementer's warm environment hides fresh-clone failures that hit CI on
    day one;
  - process leaks and signal handling;
  - tests weakened to pass (compare against base) — a weakened assertion can
    hide a real bug in the code under test;
  - tests that don't test what they claim;
  - README vs actual behaviour;
  - DRY, YAGNI and file size against the repo's rules.
- **Output:** numbered findings, most severe first. Each one has a severity
  (blocking / should-fix / nit), `file:line`, a concrete failure scenario, and
  **how it was verified** (read / ran / reproduced). Speculative findings are
  marked. If there is nothing worth fixing, the reviewer says "No findings"
  plainly and doesn't pad the list.

## 4. Triage (orchestrator)

- **Spot-check the key findings yourself** (grep or read the cited lines)
  before forwarding them. Don't forward unverified claims as orders.
- **Fix:** blocking items, should-fix items, and verified cheap nits introduced
  by the branch.
- **Don't fix in this PR**, and say why:
  - Speculative findings with no reproduction.
  - Pre-existing issues that need behaviour changes. Add them to the relevant
    follow-up ticket instead of widening the PR.
- Send the fix list back to the **same implementer** (it keeps its context),
  numbered, with the agreed resolution for each item where there was a design
  choice.
- A trivial fix (a line or two) the orchestrator can make directly. It still
  gets a review round, scoped to that commit.
- **Prove a regression check catches the bug:** run it against the base commit
  before the fix (a temporary sibling worktree), not just against the fixed
  code. Record the failure rate if it's probabilistic.
- **Workarounds for upstream bugs:** cite the ticket in the code, and file a
  follow-up to remove the workaround, blocked by the fix.

## 5. Stop condition, merge and PR

- **Stop condition:** stop when a round finds nothing worth fixing. Remaining
  items must be pre-existing, out of scope, or speculative, and must be
  ticketed.
- **Round cap:** if round 5 is still producing verified findings, stop looping.
  Report the open findings and the round history to the operator and ask how to
  proceed — more rounds, descope, or ship with ticketed known issues. Expect
  most real bugs in rounds 1–2; rounds 3+ mostly catch regressions introduced
  by fixes, and small, clean tickets converge in two rounds.
- **Fix your own commit message** if a reviewer shows it overclaims — amend and
  force-push the backup branch (or, if force-push is blocked, note the
  correction in a follow-up commit message); this stops being allowed once a
  PR is open.
- **Merging another branch in.** If a sibling ticket merges first, merge
  `origin/<base>` into the branch with a merge commit, never a rebase.
  Regenerate lockfiles with tooling. Then run a review round on the merge:
  conflict resolutions, nothing lost from either side.
- **The PR body** (template: [references/pr-body.md](references/pr-body.md))
  contains: ticket links, what changed, before/after test counts, real-run
  results including scenarios that still fail and why, the number of review
  rounds, and out-of-scope findings with the tickets they went to.
- **After the PR:**
  - Offer to subscribe to PR activity for CI and review comments.
  - When a PR merges, restart that branch from the latest base before any
    follow-up work.

## Operational notes

- "Uncommitted/unpushed changes" stop hooks will fire repeatedly while agents
  work. Leave the hook alone; push only when the loop allows it.
