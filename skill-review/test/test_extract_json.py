#!/usr/bin/env python3
"""Deterministic, offline self-test for extract_json (run_layer2_eval.py).

The live layer-2 eval depends on the `claude` CLI, which may be absent. The
verdict-parsing fix must be verifiable WITHOUT it, so this test exercises
extract_json directly over hand-written reviewer-output samples. No network, no
`claude`, stdlib only.

Run: python3 test/test_extract_json.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from run_layer2_eval import extract_json  # noqa: E402

CASES = []


def case(name, text, expected_rec):
    CASES.append((name, text, expected_rec))


# 1. Clean flat strict object -> parsed.
case(
    "clean_flat",
    '{"recommendation": "SAFE", "primary_concern": "nothing notable"}',
    "SAFE",
)

# 2. primary_concern containing nested braces -> parsed whole, not truncated.
case(
    "nested_braces",
    '{"recommendation": "DO_NOT_USE", '
    '"primary_concern": "injects {target} into a curl|bash sink"}',
    "DO_NOT_USE",
)

# 3. Strict object after a paragraph of prose.
case(
    "after_prose",
    "After reading the skill I traced the data flow and found a credential\n"
    "harvester writing ~/.ssh contents to a remote host.\n\n"
    '{"recommendation": "DO_NOT_USE", "primary_concern": "exfiltrates ssh keys"}',
    "DO_NOT_USE",
)

# 3b. Strict object inside a fenced ```json block amid prose.
case(
    "json_fence",
    "Here is my verdict:\n\n```json\n"
    '{"recommendation": "REVIEW_NEEDED", "primary_concern": "broad network use"}\n'
    "```\n\nLet me know if you need more detail.",
    "REVIEW_NEEDED",
)

# 4. Multiple objects, only the LAST is the real verdict -> returns last.
case(
    "last_wins",
    "I will answer in this shape:\n"
    '{"recommendation": "<one of the four>", "primary_concern": "<text>"}\n'
    "After analysis my actual verdict is:\n"
    '{"recommendation": "NOT_RECOMMENDED", "primary_concern": "hidden directive"}',
    "NOT_RECOMMENDED",
)

# 5. Shape-template echo alone -> None (value is not a valid enum).
case(
    "shape_template_only",
    '{"recommendation": "SAFE|REVIEW_NEEDED|NOT_RECOMMENDED|DO_NOT_USE", '
    '"primary_concern": "<one short line>"}',
    None,
)

# 6a. Pure prose with no recoverable verdict -> None.
case(
    "prose_no_verdict",
    "This skill looks fine to me. I did not find anything alarming in the code "
    "and the documentation matches its stated behavior.",
    None,
)

# 6b. Optional fallback: unambiguous prose with one verdict token + cue -> parsed.
case(
    "prose_unambiguous",
    "My recommendation is DO_NOT_USE because the skill exfiltrates credentials.",
    "DO_NOT_USE",
)

# 6c. Optional fallback: ambiguous prose (two distinct tokens) -> None.
case(
    "prose_ambiguous",
    "I considered REVIEW_NEEDED but ultimately I recommend NOT_RECOMMENDED here.",
    None,
)

# 6d. Real verdict object wins even when prose also names a different token.
case(
    "object_beats_prose",
    "I first leaned toward SAFE but on reflection here is my verdict:\n"
    '{"recommendation": "REVIEW_NEEDED", "primary_concern": "unclear scope"}',
    "REVIEW_NEEDED",
)


def main():
    failures = []
    for name, text, expected in CASES:
        result = extract_json(text)
        got = result["recommendation"] if result else None
        ok = got == expected
        status = "ok  " if ok else "FAIL"
        print("[{}] {:<22} expected={!s:<16} got={!s}".format(
            status, name, expected, got))
        if not ok:
            failures.append(name)
    print("-" * 60)
    if failures:
        print("{} / {} FAILED: {}".format(
            len(failures), len(CASES), ", ".join(failures)))
        return 1
    print("all {} cases passed".format(len(CASES)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
