#!/usr/bin/env python3
"""Measure scan.py's false-positive rate on a folder of REAL (benign) skills.

Track-2 of testing: recall is measured on synthetic fixtures (run_eval.py);
precision is measured on real, benign skills — which are abundant and not taken
down. Point this at a directory tree containing real skills; it finds every
SKILL.md, scans its directory, and reports the verdict distribution and the
"serious" verdicts (the false positives to inspect).

Usage:
    python3 test/precision_scan.py <dir> [<dir> ...]
    # e.g. python3 test/precision_scan.py ~/.claude

This scans local directories only (no network). It does NOT execute any scanned
skill — scan.py reads files as text. Requirements: Python 3.6+, stdlib only.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
sys.path.insert(0, SKILL_DIR)
import scan  # noqa: E402

SERIOUS = {"REVIEW_NEEDED", "NOT_RECOMMENDED", "DO_NOT_USE"}


def find_skill_dirs(roots):
    seen = []
    for root in roots:
        root = os.path.expanduser(root)
        for dirpath, _dirs, files in os.walk(root):
            if any(f.lower() == "skill.md" for f in files):
                if dirpath not in seen:
                    seen.append(dirpath)
    return seen


def main(argv):
    if not argv:
        print("usage: precision_scan.py <dir> [<dir> ...]", file=sys.stderr)
        return 2
    dirs = find_skill_dirs(argv)
    if not dirs:
        print("no SKILL.md found under: " + ", ".join(argv), file=sys.stderr)
        return 1

    dist = {}
    serious = []
    for d in dirs:
        try:
            rep = scan.build_report(d)
        except Exception as exc:  # never let one bad tree kill the run
            print(f"  (error scanning {d}: {exc})", file=sys.stderr)
            continue
        rec = rep["recommendation"]
        dist[rec] = dist.get(rec, 0) + 1
        if rec in SERIOUS:
            s = rep["severity_summary"]
            cats = ",".join(sorted(rep["category_summary"]))
            serious.append((os.path.basename(d), rec, s["CRITICAL"], s["HIGH"], cats))

    n = len(dirs)
    fp = len(serious)
    print(f"\nScanned {n} real skills")
    print("Verdict distribution:", dict(sorted(dist.items())))
    print(f"\nFlagged 'serious' (candidate false positives): {fp}/{n} "
          f"= {100 * fp / n:.0f}%")
    for name, rec, c, h, cats in sorted(serious):
        print(f"  {name:<30} {rec:<16} C={c} H={h}  [{cats}]")
    print("\nNote: on a benign corpus, every 'serious' verdict is a false positive "
          "of the\nstatic layer — the empirical reason the skill's verdict must "
          "come from layer-2\nreasoning, not from scan.py output.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
