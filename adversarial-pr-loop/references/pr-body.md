# PR body template

```markdown
## Tickets

- {parent ticket link}
- {child ticket links}

## What changed

{short prose: the shape of the change, judgement calls made and why}

Size: {estimate} non-test lines estimated, {actual} actual{; if over 2×: what
grew and the operator's decision to continue}.

## Tests

| Suite | Base | This branch |
|---|---|---|
| {suite} | {n passed} | {n passed} |

New/changed tests: {what they cover, and proof the regression checks fail on
the base commit — include the failure rate if probabilistic}

## Real-run results

{what was actually run end-to-end, what worked, and the scenarios that still
fail and why}

## Review

{N} adversarial review rounds; final round clean.
{Any early stop — churn, size, round cap — and what the operator decided.}

Known limits (rare findings documented instead of fixed):
- {limit} — {why it is rare}

Out of scope (ticketed, not fixed here):
- {finding} → {ticket link}
```
