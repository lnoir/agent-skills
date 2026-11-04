---
name: adversarial-pr-loop
description: Implement tickets with subagents, then run fresh adversarial reviewers until a round comes back clean, then raise the PR. Operator-invoked only — run this loop when explicitly asked to (e.g. /adversarial-pr-loop, or "use the adversarial PR loop"); never adopt it on your own judgement. Suited to sessions with no local worktree (where agent-pairing can't run), and to critical work generally — including as a review stage after an agent-pairing run.
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
  alone: diagnosed breakages, file paths, a "Done when" section, and a rough
  **size estimate** (non-test lines). The estimate is what the size tripwire in
  §5 measures against, and the ticket text is what every reviewer gets (§3).
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
- **The brief:** the ticket's goal, "Done when" and size estimate, verbatim.
  Claims alone let a reviewer check the branch only against itself: round
  after round can harden a mechanism that defeats the ticket's purpose, and
  no one notices because every finding is internally consistent.
- **Claims to check:** what the branch says it does, so the reviewer verifies
  claims instead of trusting them.
- **Previous findings:** a list of them, to confirm they are actually fixed and
  that the fixes caused no regressions.
- **An explicit out-of-scope list:** pre-existing issues already ticketed, and
  settled decisions. Without it, every round re-raises the same things.
- **Targeted hunts**, for example:
  - goal and proportion — does the branch still serve the brief, and is any
    mechanism out of proportion to the problem it solves? A finding here is
    as valid as a bug;
  - a fresh-clone run of install plus the full test suite — high value: the
    implementer's warm environment hides fresh-clone failures that hit CI on
    day one. Run it as a **different user from the implementer**, and as root
    if the implementer wasn't: containers and cloud sessions run as root, and
    permission-based fixtures (`chmod 000`) pass for one user and fail for
    the other;
  - process leaks and signal handling;
  - tests weakened to pass (compare against base) — a weakened assertion can
    hide a real bug in the code under test;
  - tests that don't test what they claim;
  - README vs actual behaviour;
  - DRY, YAGNI and file size against the repo's rules.
- **Output:** numbered findings, most severe first. Each one has a severity
  (blocking / should-fix / nit), a **likelihood** (common / plausible / rare),
  `file:line`, a concrete failure scenario, and **how it was verified** (read /
  ran / reproduced). Speculative findings are marked. A finding inside code a
  previous fix round added says so. If there is nothing worth fixing, the
  reviewer says "No findings" plainly and doesn't pad the list.

## 4. Triage (orchestrator)

- **Spot-check the key findings yourself** (grep or read the cited lines)
  before forwarding them. Don't forward unverified claims as orders.
- **Fix:** blocking items, should-fix items, and verified cheap nits introduced
  by the branch.
- **Prefer removal.** For a finding inside code this branch added, first ask
  whether deleting or narrowing that code, or documenting a limit, resolves
  it. A fix that brings in a new mechanism (a state machine, a retry counter,
  a scan with bounds) needs a stated reason, and it is the usual source of the
  next round's finding.
- **Weigh likelihood, not just severity.** A `rare` finding (it needs an
  unusual environment or a hand-made fault, such as an unwritable `refs/`) goes
  to the PR's known-limits list by default. Fix it only when the fix is cheap
  or the consequence is data loss or something outward-facing.
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
  ticketed, or rare and listed in the PR's known limits.
- **Round cap:** if round 5 is still producing verified findings, stop looping.
  Report the open findings and the round history to the operator and ask how to
  proceed — more rounds, descope, or ship with ticketed known issues. Expect
  most real bugs in rounds 1–2; rounds 3+ mostly catch regressions introduced
  by fixes, and small, clean tickets converge in two rounds.
- **Earlier stops: ask the operator before another round when either fires.**
  - **Churn:** two rounds in a row whose findings sit in code the previous
    fix round added. The loop is now hardening its own fixes; the usual way out
    is to remove that mechanism, not to patch it again.
  - **Size:** the branch's non-test diff passes 2× the ticket's size
    estimate. Report the estimate, the actual size and what grew, and let the
    operator choose between descoping and continuing.
- **Follow-up fixes after the loop** (the operator's or a PR reviewer's small
  asks once the PR is open) get one scoped review of the new commit, not a
  fresh loop. Re-enter the full loop only if the fix grows past the files it
  was asked to touch.
- **Fix your own commit message** if a reviewer shows it overclaims — amend and
  force-push the backup branch (not allowed once a PR is open), or, if
  force-push is blocked, note the correction in a follow-up commit message.
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
