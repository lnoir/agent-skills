---
name: strange-mode
description: Create multiple parallel implementation variations using git worktrees or sandbox playgrounds for comparing architectural approaches
---

# Strange Mode - Parallel Universes

Strange Mode is a workflow for exploring multiple variations of a solution concurrently. It operates on a strict **Orchestrator-Subagent** model: the Orchestrator defines the problem boundaries, while independent sub-agents design and execute the variations.

---

## Shared Principles (Core Discipline)

Before selecting an execution mode, you must adhere to these behavioral rules. Failure to do so leads to overlapping context and over-directed designs.

### Rule A: Delegation is Mandatory
*   The Orchestrator **must not** write implementation code.
*   The Orchestrator's sole job is to define the target problem, spawn parallel sub-agents, compile the results, present them to the user, and collapse the non-surviving universes.
*   Each sub-agent runs in an isolated workspace with no knowledge of other universes.

### Rule B: Zero Solution Prescription
*   The Orchestrator **must not** specify the details of the variations (e.g., "Use gyroscopes for Option 5").
*   Instead, document requirements, style constraints (design tokens), performance limits, and interface APIs. Let the sub-agents independently decide their creative direction.

### Rule C: Plan the Problem, Not the Answers
*   The initial plan must define:
    1.  **The Shared Container**: Target folder, naming conventions (e.g. `option-N.html`), and file constraints.
    2.  **The Constraints**: CSS styling tokens, allowed dependencies, performance budgets.
    3.  **The Exclusion List**: Track techniques or approaches already covered in prior rounds. This forces divergence during multi-round iteration.

---

## Mode Selection

Analyze the task requirements using this crisp binary choice to determine which execution mechanics file to read next:

*   **Do variations modify the same files differently?** 
    $\rightarrow$ **Code Mode**. Follow the instructions in [modes/code.md](modes/code.md).
*   **Are variations new standalone files that can coexist in the same folder?** 
    $\rightarrow$ **Artifact Mode**. Follow the instructions in [modes/artifact.md](modes/artifact.md).
