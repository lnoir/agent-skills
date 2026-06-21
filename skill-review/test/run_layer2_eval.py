#!/usr/bin/env python3
"""Blind layer-2 (reasoning) eval for skill-review.

The static layer (scan.py, gated by run_eval.py) is a noisy candidate generator.
The load-bearing part of skill-review is layer 2: an agent reading a skill and
reasoning about whether its behavior matches its stated intent - escalating
malicious skills the static layer only warns on or misses, and CLEARING benign
skills that merely *discuss* prompts/commands (which the static layer
false-flags). This harness measures that reasoning layer.

It is a DEV/EVAL TOOL, not a deterministic unit test. The shipped skill stays
stdlib-only; this tool additionally depends on the `claude` CLI as the reviewing
agent. If `claude` is missing or a call fails/times out, the affected fixture is
recorded as skipped and the run continues - it never crashes.

BLINDING (see layer2_blind.py). The reviewing agent must not be able to tell a
fixture is a test, nor see its label. For each fixture we:
  1. copy it to a fresh temp dir with a NEUTRAL name (skill-under-review-<n>),
     never the fixture's real name or tier;
  2. strip every line containing the sentinel `SKILL-REVIEW-FIXTURE`;
  3. present the files as "a skill a user is considering installing", with the
     review methodology but NO mention of tests, fixtures, or expected answers.
Ground-truth labels live only in manifest.py and are used solely for scoring,
never shown to the reviewer.

Usage:
    python3 test/run_layer2_eval.py --dry-run   # offline: print neutral prompts
    python3 test/run_layer2_eval.py             # live: invoke the claude CLI
    python3 test/run_layer2_eval.py --runs 3    # majority-vote over N runs

Requirements: Python 3.6+, stdlib only (the `claude` CLI for live runs).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from manifest import MALICIOUS, BENIGN  # noqa: E402
from layer2_blind import (  # noqa: E402
    load_methodology, neutralize_dir, gather_files, build_prompt,
)

SERIOUS = {"REVIEW_NEEDED", "NOT_RECOMMENDED", "DO_NOT_USE"}
VALID_RECS = {"SAFE"} | SERIOUS

# Cases the static layer false-flagged; layer 2 must CLEAR these (call them out).
STATIC_FALSE_FLAGS = {"security-docs", "weather-client"}


def extract_json(text):
    """Pull the recommendation object out of reviewer stdout."""
    for m in re.finditer(r"\{[^{}]*\"recommendation\"[^{}]*\}", text, re.DOTALL):
        try:
            obj = json.loads(m.group(0))
        except ValueError:
            continue
        rec = str(obj.get("recommendation", "")).strip().upper()
        if rec in VALID_RECS:
            return {"recommendation": rec,
                    "primary_concern": str(obj.get("primary_concern", "")).strip()}
    return None


def call_reviewer(prompt, timeout):
    """Invoke `claude -p`. Returns (parsed|None, error|None)."""
    if shutil.which("claude") is None:
        return None, "claude CLI not found on PATH"
    cmd = ["claude", "-p", prompt, "--output-format", "json"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "timed out after {}s".format(timeout)
    except OSError as exc:
        return None, "exec failed: {}".format(exc)
    if proc.returncode != 0:
        return None, "exit {}: {}".format(proc.returncode,
                                          (proc.stderr or "").strip()[:200])
    stdout = proc.stdout or ""
    # --output-format json wraps the agent text in {"result": "..."}; unwrap it.
    inner = stdout
    try:
        env = json.loads(stdout)
        if isinstance(env, dict) and isinstance(env.get("result"), str):
            inner = env["result"]
    except ValueError:
        pass
    parsed = extract_json(inner) or extract_json(stdout)
    if parsed is None:
        return None, "could not parse JSON from reviewer output"
    return parsed, None


def majority(recs):
    counts = {}
    for r in recs:
        counts[r] = counts.get(r, 0) + 1
    return max(counts.items(), key=lambda kv: (kv[1], kv[0] != "SAFE"))[0]


def review_fixture(kind, name, idx, methodology, args):
    """Neutralize one fixture and (unless dry-run) review it. Returns a dict."""
    src = os.path.join(HERE, "fixtures", kind, name)
    tmp = tempfile.mkdtemp(prefix="skill-under-review-{}-".format(idx))
    try:
        dst = os.path.join(tmp, "skill-under-review-{}".format(idx))
        neutralize_dir(src, dst)
        prompt = build_prompt(methodology, gather_files(dst))
        if args.dry_run:
            return {"prompt": prompt}
        recs, concerns, errors = [], [], []
        for _ in range(args.runs):
            parsed, err = call_reviewer(prompt, args.timeout)
            if err:
                errors.append(err)
            else:
                recs.append(parsed["recommendation"])
                concerns.append(parsed["primary_concern"])
        if not recs:
            return {"skipped": True, "error": errors[-1] if errors else "unknown"}
        rec = majority(recs)
        concern = concerns[recs.index(rec)]
        return {"recommendation": rec, "primary_concern": concern, "votes": recs}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_dry(methodology, args):
    print("=" * 72)
    print("  layer-2 eval DRY RUN - neutralized reviewer prompts (no API calls)")
    print("=" * 72)
    items = [("malicious", n) for n in sorted(MALICIOUS)] + \
            [("benign", n) for n in sorted(BENIGN)]
    show = items if args.all else items[:1]
    for idx, (kind, name) in enumerate(show):
        res = review_fixture(kind, name, idx, methodology, args)
        print("\n" + "#" * 72)
        print("# fixture #{} (neutralized; real name/tier hidden)".format(idx))
        print("#" * 72)
        print(res["prompt"])
    print("\n(Use --all to print every fixture's prompt; default shows one.)")
    return 0


def score_rows(rows):
    """Print the per-fixture table and return summary counters."""
    print("\n{:<22} {:<16} {:<8} {}".format(
        "fixture", "verdict", "correct", "primary_concern"))
    print("-" * 72)
    mal_total = mal_caught = ben_total = ben_clean = skipped = 0
    cleared = {}
    for r in rows:
        if r.get("skipped"):
            skipped += 1
            print("{:<22} {:<16} {:<8} {}".format(
                r["name"][:22], "SKIPPED", "-", r.get("error", "")[:30]))
            continue
        rec = r["recommendation"]
        serious = rec in SERIOUS
        if r["kind"] == "malicious":
            mal_total += 1
            correct = serious
            mal_caught += int(correct)
        else:
            ben_total += 1
            correct = not serious
            ben_clean += int(correct)
            if r["name"] in STATIC_FALSE_FLAGS:
                cleared[r["name"]] = (not serious, rec)
        print("{:<22} {:<16} {:<8} {}".format(
            r["name"][:22], rec, "yes" if correct else "NO",
            r["primary_concern"][:34]))
    return mal_caught, mal_total, ben_clean, ben_total, skipped, cleared


def run_live(methodology, args):
    print("=" * 72)
    print("  skill-review LAYER-2 (reasoning) eval  -  reviewer: claude CLI")
    print("  runs/fixture={}  per-call timeout={}s".format(args.runs, args.timeout))
    print("=" * 72)
    if shutil.which("claude") is None:
        print("\nclaude CLI not found on PATH. Cannot run the live eval.")
        print("Re-run with --dry-run to inspect the (offline) neutralized prompts.")
        return 0  # graceful: absence of reviewer is not a harness failure

    rows = []
    idx = 0
    for kind, labels in (("malicious", MALICIOUS), ("benign", BENIGN)):
        for name in sorted(labels):
            res = review_fixture(kind, name, idx, methodology, args)
            res.update(kind=kind, name=name)
            rows.append(res)
            idx += 1

    mal_caught, mal_total, ben_clean, ben_total, skipped, cleared = score_rows(rows)
    print("-" * 72)
    rc = "{}/{}".format(mal_caught, mal_total) if mal_total else "n/a"
    pr = "{}/{}".format(ben_clean, ben_total) if ben_total else "n/a"
    print("  Recall (malicious -> serious)    : {}".format(rc))
    print("  Precision (benign -> non-serious): {}".format(pr))
    print("  Skipped (reviewer unavailable)   : {}".format(skipped))
    print("\n  Static-layer false-flags - did layer 2 CLEAR them?")
    for name in sorted(STATIC_FALSE_FLAGS):
        if name in cleared:
            ok, rec = cleared[name]
            print("    {:<16} {}  ({})".format(
                name, "CLEARED" if ok else "still flagged", rec))
        else:
            print("    {:<16} (skipped / not reviewed)".format(name))
    return 0


def main(argv):
    ap = argparse.ArgumentParser(description="Blind layer-2 eval for skill-review.")
    ap.add_argument("--dry-run", action="store_true",
                    help="print neutralized reviewer prompt(s); no API calls")
    ap.add_argument("--all", action="store_true",
                    help="with --dry-run, print every fixture's prompt")
    ap.add_argument("--runs", type=int, default=1,
                    help="repeat each fixture N times and majority-vote (default 1)")
    ap.add_argument("--timeout", type=int, default=120,
                    help="per-call reviewer timeout in seconds (default 120)")
    args = ap.parse_args(argv)
    if args.runs < 1:
        ap.error("--runs must be >= 1")
    methodology = load_methodology(SKILL_DIR)
    if args.dry_run:
        return run_dry(methodology, args)
    return run_live(methodology, args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
