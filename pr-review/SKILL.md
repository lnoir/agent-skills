---
name: pr-review
description: Review a colleague's pull request. Checks the PR head out into an isolated worktree, fans out specialist subagents (correctness, security, quality) that return structured findings, verifies those findings against the real code before you see them, then drafts self-contained review comments in your own voice for approval. Triggered by "review this PR", "review PR 123", "do a code review", or /pr-review.
---

# PR Review

## Overview

Review a pull request with a fan-out/verify pipeline rather than one broad pass:

1. Check the PR head out into an **isolated git worktree** so every local read hits the PR's code — not the ambient checkout — without touching your working tree.
2. Fan out **specialist subagents** (correctness, security, quality+performance), chosen dynamically for the diff. Each returns **structured findings**, not drafted prose.
3. The main agent runs a **verification sweep** — dedupe, confirm each finding against the real code (kill false positives), and gap-check (including completeness against the Jira ticket).
4. The main agent **drafts the surviving findings once, centrally, in the operator's voice** — self-contained comments, no separate reasoning blurb.
5. You approve; it posts as a single review and tears the worktree down.

## Prerequisites

- `gh` (GitHub CLI) installed and authenticated (`gh auth status`)
- `python3` (used for parsing the patch listing)
- Run from within a local clone of the repo the PR belongs to
- `acli` (Atlassian CLI) authenticated — optional, for Jira context

## Workflow

### Step 1: Identify the PR and materialise it in a worktree

If the user gave a PR number, use it. Otherwise ask:

> Which PR would you like me to review? (provide a PR number)

Fetch metadata:

```bash
PR=<number>

gh pr view $PR --json number,title,body,headRefName,headRefOid,baseRefName,author,additions,deletions,changedFiles,url
```

Derive `REPO` (`owner/name`) from the `url` field of that response — in a checkout whose `origin` is a fork, `gh repo view` would return the fork while the PR lives upstream, and every later API call would miss. Capture `headRefName` here too; nothing later should re-fetch metadata already in hand.

**Pin the review to one commit.** Capture `headRefOid` as `HEAD_SHA` now. Everything downstream — patches, the worktree, verification, posting — must describe this one revision; a push to the PR mid-review would otherwise mix revisions silently.

Fetch the full file list and all patches in a **single API call** (`per_page=100` avoids the 30-file default). Every file's patch is in this one response — do not make per-file calls:

```bash
gh api "/repos/$REPO/pulls/$PR/files?per_page=100&page=1" | python3 -c "
import json, sys
files = json.load(sys.stdin)
for f in files:
    print(f['filename'], f.get('status'), f.get('additions'), f.get('deletions'))
    if f.get('patch'):
        print(f['patch'])
    print('---')
"
```

If `changedFiles` exceeds 100, fetch `page=2` (rare).

**Materialise the PR head in an isolated, detached worktree.** Use the `pull/<n>/head` ref, which exists on the base repo even for cross-repo forks, so this is fork-safe and never switches your current branch. Check out detached at the pinned SHA — no named branch, so nothing can collide with (or later delete) a branch the operator already owns:

```bash
PRIMARY=$(git rev-parse --show-toplevel)
SCRATCH=$(mktemp -d)
WORKTREE=$SCRATCH/pr-$PR
git fetch origin "pull/$PR/head"
git worktree add --detach "$WORKTREE" "$HEAD_SHA"
```

If the fetch doesn't contain `$HEAD_SHA` (the PR was pushed between the metadata call and the fetch), re-fetch the metadata and restart from the new `headRefOid` rather than reviewing a mix. Verify the checkout matches the pin — this must fail the command, not just print:

```bash
[ "$(git -C "$WORKTREE" rev-parse HEAD)" = "$HEAD_SHA" ] || { echo "MISMATCH: worktree is not $HEAD_SHA — stop"; exit 1; }
```

**Make dependency source readable.** A fresh worktree only contains *tracked* files, so `node_modules` (gitignored) is absent — and verifying how a dependency actually behaves is often what a review turns on (see Step 3). Symlink the primary clone's modules in:

```bash
[ -d "$PRIMARY/node_modules" ] && ln -s "$PRIMARY/node_modules" "$WORKTREE/node_modules"
```

If this PR changes `package.json` or the lockfile, the primary clone's `node_modules` is the *old* version — and the dependency's behaviour may be exactly what changed. In that case, flag it and prefer a real install in the worktree over the symlink — **with lifecycle scripts disabled**. The worktree isolates files, not execution: a plain install runs `postinstall` and friends from PR-controlled dependencies, i.e. executes the code you are reviewing. Install for *reading*, never run the PR:

```bash
npm ci --ignore-scripts        # or: pnpm install --ignore-scripts / yarn --ignore-scripts, per the lockfile
```

`--ignore-scripts` means native addons won't build — fine, since the review only reads source. Never run the PR's own build, test, or install scripts as part of inspection.

**All local file reads from here on — specialists and the verification sweep — resolve against `$WORKTREE`, never the ambient checkout.** Read files as `$WORKTREE/<path>`, including `$WORKTREE/node_modules/<pkg>` for dependency source.

Present a brief summary:

```
PR #<number>: <title>
Author: @<username>
Branch: <head> → <base>
Changes: +<additions> -<deletions> across <N> files
Worktree: <WORKTREE>

Files changed:
- src/foo.ts (+12 -3)
- src/bar.ts (+45 -20)
```

Path by size:
- **≤10 files**: proceed directly.
- **11–20 files**: proceed.
- **>20 files**: ask whether to focus on specific areas before fanning out; scope the specialists to the agreed subset.

Always verify the file count returned matches `changedFiles`; paginate only if they differ.

### Step 2: Jira context (optional)

```bash
acli --version 2>/dev/null || echo "NOT_INSTALLED"
```

If not installed, say so once and continue without it. If available, extract the ticket key from the branch name already fetched in Step 1 (`headRefName`, pattern `[A-Z]+-[0-9]+`) and fetch it:

```bash
acli jira issue view <TICKET-KEY> --output json
```

Use the ticket to understand intent, acceptance criteria, and scope. It feeds the completeness gap-check in the sweep (Step 4). If no key is found, skip silently.

### Step 3: Fan out specialist subagents

**Pick the lenses for this diff (dynamic).** Don't run lenses that can't find anything:
- **Correctness** — always.
- **Security** — unless the diff has no executable or config surface (e.g. docs/comments only).
- **Quality (+performance)** — unless the change is trivial (e.g. config/copy only) with no maintainability or performance surface.

State which lenses you're running and why any were skipped.

Launch the chosen specialists **in parallel** (one message, multiple `Agent` calls, `general-purpose` type, **high reasoning effort** — depth is where this pipeline earns its cost). Give each the same context and a lens-specific brief:

- PR title/body, ticket summary (if any), base→head
- The changed-file list and their patches
- The worktree path — instruct them to read full file content from `$WORKTREE/<path>`, using patches to locate changes and the full files for surrounding context
- Their lens's focus (below)
- The **standing rules** and **output contract** (below), and that `evidence` is internal — it will be consumed by the verification sweep, never shown to the user

**Standing rules for every specialist:**

- **Verify library behaviour at its source — don't trust the API name.** When a claim, or any call on a critical path, depends on how a dependency behaves, read that dependency's actual source under `$WORKTREE/node_modules/<pkg>` and cite `file:line`. Do not assume `.skip()/.take()`, an ORM, a serializer, a cache, or a framework lifecycle does what its name implies — *especially under this diff's specific conditions*: joins, large N, nulls, empty input, concurrency. Behaviour that only holds on the un-joined / small-N / non-null path is a bug, not a pass.
- **Pull the thread — depth over breadth.** Trace critical control and data flow end-to-end: handler → query builder → emitted SQL → DB limits, or equivalent for the change at hand. One finding grounded all the way to source is worth more than five shallow ones. If a critical path is suspicious, follow it to the bottom before moving on.

**Correctness** — logic/flow/coherence; edge cases; off-by-one, null/undefined, incorrect assumptions; does behaviour match apparent intent and the ticket; and **test-coverage adequacy** (happy path, edges, error conditions for the changed behaviour).

**Security** — input validated at the boundary; injection, exposure of secrets/PII, privilege escalation, unsafe deserialisation, SSRF, authz gaps.

**Quality (+performance)** — readability and structure; unnecessary duplication or over-complexity; adherence to conventions **already established in this file/codebase**; maintainability; and performance (needless allocation/iteration, N+1s, blocking work on hot paths, avoidable async waterfalls).

**Output contract** — each specialist returns only this JSON:

```json
{
  "lens": "correctness | security | quality",
  "findings": [
    {
      "file": "src/foo.ts",
      "line": 42,
      "side": "RIGHT",
      "severity": "blocking | non-blocking | suggestion",
      "claim": "one line: what is wrong",
      "evidence": "why it is wrong — the reasoning/proof, INTERNAL, feeds the sweep only",
      "suggested_fix": "the concrete change"
    }
  ]
}
```

`line` is a file line number in the PR head (`side: "RIGHT"`); use `side: "LEFT"` with a base-file line only for a comment on deleted code. Empty `findings` is a valid, expected result. Do not manufacture issues.

### Step 4: Verification sweep (main agent)

This is an active investigation, not a filter over what the lenses happened to raise. Run it at **high reasoning effort**. Before the user sees anything:

1. **Re-derive the PR's purpose.** From the ticket and the diff, state in one line what problem this PR exists to fix (e.g. "the score-sorted query times out for high-volume companies"). You'll test the fix against this, not just against the lenses' findings.
2. **Dedupe / merge** — collapse the same issue flagged by more than one lens into one finding, keeping the highest severity.
3. **Verify against the real code** — for each finding, open `$WORKTREE/<file>` and confirm the claim holds in context; for any finding that turns on dependency behaviour, confirm it against `$WORKTREE/node_modules/<pkg>` source. Specialists reason partly from patches and will occasionally hallucinate or miss surrounding code. **Drop anything you cannot confirm.** While confirming, also verify the **anchor**: the `line`/`side` must point at the exact line in the worktree (i.e. in `$HEAD_SHA`) that the claim is about, and that line must appear in the PR's diff — GitHub rejects review comments on undiffed lines. Correct the anchor from the real file if the specialist's is off.
4. **Adversarial path check** — for each critical-path change, ask: *on which code paths does the fix silently not apply, or reintroduce the very problem it exists to fix?* Enumerate the paths (joined vs un-joined, filtered vs unfiltered, large vs small N, empty input). This is the question that catches the bug that "works on the path the author tested." For any blocking or critical-path finding, **spawn a targeted deep-verify subagent** (`general-purpose`, high effort, worktree + `node_modules` access) to trace it to source and confirm or refute.
5. **Gap-check** — look for what the lenses collectively missed, and check **completeness against the ticket**: are all acceptance criteria implemented? Anything in scope but missing, or present but out of scope? Add confirmed gaps as findings.

Only findings that survive this sweep proceed to drafting.

### Step 5: Draft comments in the operator's voice

Draft **once, here** (not in the specialists) so voice stays consistent.

**Load the operator's comment style.** Read the operator's `CLAUDE.md` (`~/.claude/CLAUDE.md` and any repo-level `CLAUDE.md`) and honour any PR-review / comment-style section. If none is specified, default to [Conventional Comments](https://conventionalcomments.org): `label (decoration): subject` — labels `praise|nitpick|suggestion|issue|question|thought|todo|chore`, decorations `(blocking)|(non-blocking)|(if-minor)`.

**Every comment must be self-contained.** The bar: *if the operator can't verify the claim from the comment plus its file:line alone, the comment isn't finished.* The comment carries what's wrong, where, the consequence, and the fix — then stops. There is **no separate "reasoning" field** in the output; the specialists' `evidence` stays internal.

Draft format:

```
---
File: src/foo.ts:42   (or "General" for PR-level)

<the comment exactly as it will appear on the PR>
```

If nothing survives the sweep, say so plainly — "no issues found" is a useful outcome.

### Step 6: Confirm and post

Present all drafts and ask:

> Which comments would you like to post? (e.g. "all", "1 and 3", "none")
> Approve, request changes, or comment-only?

Accept edits to any draft before posting. Post the selected comments as a **single review**.

**Recheck the pin before posting.** Fetch `headRefOid` again; if it no longer equals `$HEAD_SHA`, the PR changed under you — tell the user and offer to re-verify against the new head rather than posting a review of a stale revision.

**Build the payload as a JSON file, never inline in shell.** Comment bodies contain backticks, `$`, and quotes; embedded in shell-quoted command text they break or — worse — get expanded and executed. Write the payload with the Write tool (not a heredoc) to `$SCRATCH/review-payload.json` and pass it with `--input`. Anchored drafts go in `comments`; any approved "General" (PR-level) draft folds into the review `body` alongside the summary — the reviews API has no unanchored comment entry. Include `commit_id` — without it GitHub attaches the review to whatever the latest commit is — and use `line`/`side` (file line numbers, as verified in the sweep), not the deprecated `position` diff offsets. Do **not** use repeated `-f "comments[]..."` flags (each `[]` creates a separate orphaned entry and the API rejects it):

```json
{
  "commit_id": "<HEAD_SHA>",
  "body": "<overall review summary>",
  "event": "<APPROVE|REQUEST_CHANGES|COMMENT>",
  "comments": [
    {"path": "<file1>", "line": <line1>, "side": "RIGHT", "body": "<comment 1>"},
    {"path": "<file2>", "line": <line2>, "side": "RIGHT", "body": "<comment 2>"}
  ]
}
```

```bash
gh api "/repos/$REPO/pulls/$PR/reviews" --input "$SCRATCH/review-payload.json"
```

### Step 7: Tear down

Remove the worktree, on success **and** on failure. The checkout is detached, so there is no branch to delete — remove only what this run created, never a branch or file that might belong to the operator:

```bash
git worktree remove "$WORKTREE" --force
git worktree prune
rm -rf "$SCRATCH"
```

## Important notes

- Fetch all patches in a **single** API call — never loop per file. Read file bodies from `$WORKTREE`, not further API calls.
- Local reads must resolve against `$WORKTREE`. If the worktree failed to set up, stop — do not silently fall back to the ambient checkout.
- Verify dependency behaviour at its source (`$WORKTREE/node_modules/<pkg>`), don't trust API names — this is what separates a deep review from a shallow one. If the PR changes the lockfile, install in the worktree rather than symlinking — always with `--ignore-scripts`. Inspect the PR; never execute it.
- The whole review describes one commit: pin `$HEAD_SHA` at the start, verify the worktree matches, recheck before posting, and post with `commit_id` and `line`/`side` anchors.
- Specialists return findings, not prose. The main agent verifies before anything reaches the user.
- Comments are self-contained; no reasoning blurb is shown.
- Don't flag style that's consistent with existing codebase conventions. Don't suggest refactors outside the PR's scope.
- Never post without explicit approval. Always tear the worktree down.
