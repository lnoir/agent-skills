# Strange Mode: Code Mode (Git Worktrees)

Use this mode when variations require modifying existing files in the repository differently (e.g. comparing database backends or state manager architectures).

## Execution Pattern

### Step 1: Branch and Add Worktrees
For each variation, create a new branch and checked-out directory:
```bash
git branch feature/universe-[name]
git worktree add ../[project]-[name] feature/universe-[name]
```

### Step 2: Symlink Node Modules
If the project uses `node_modules`, avoid duplicating them across folders. Symlink to the main repository:
```bash
cd ../[project]-[name]
ln -s ../[project]/node_modules node_modules
```

### Step 3: Run Sub-Agents (Batched)
Spawn a dedicated sub-agent in each worktree to execute the brief independently.
*   **Batching Warning**: Batch sub-agent launches (e.g. max 3 concurrent) to respect provider concurrency limits and prevent hitting `429` rate limit errors during initialization.
*   **Recovery**: If a sub-agent fails (model unreachable, rate limit), wait for the current batch to complete, then re-spawn failed sub-agents in a fresh batch. Do not retry into an already-full concurrency window.

### Step 4: Verify and Document
Each sub-agent must, as part of its brief:
1.  **Verify** its implementation by running the project's build/test/lint scripts. Report the commands run and their outcomes.
2.  **Document** its variation in a `UNIVERSE.md` file within its worktree, covering:
    *   Unique approach characteristics
    *   Key implementation differences
    *   Pros/cons trade-offs

### Step 5: Merge and Collapse
1. Present the comparison matrix to the user.
2. Once a survivor is chosen, merge it into the target branch:
   ```bash
   git checkout main
   git merge feature/universe-[winner] --no-ff -m "Merge [winner] implementation"
   ```
3. Remove the worktrees and delete the pruned branches:
   ```bash
   git worktree remove ../[project]-[name]
   git branch -D feature/universe-[name]
   git worktree prune
   ```
