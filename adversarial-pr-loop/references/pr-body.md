# PR body template

```markdown
## Tickets

- {parent ticket link}
- {child ticket links}

## What changed

{short prose: the shape of the change, judgement calls made and why}

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

Out of scope (ticketed, not fixed here):
- {finding} → {ticket link}
```
