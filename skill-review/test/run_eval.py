#!/usr/bin/env python3
"""Offline eval harness for skill-review's static layer (scan.py).

Runs the scanner over a labeled fixture corpus and reports, honestly:
  - RECALL: of the malicious fixtures the static layer is *expected* to catch as
    serious, how many it does (a regression gate).
  - GAPS: malicious classes the static layer only flags at WARNING level, or
    misses entirely — these need the layer-2 reasoning step. Reported, not failed.
  - PRECISION: benign fixtures must not be flagged serious (a false-positive gate),
    except documented known-FP cases (e.g. a skill that *discusses* injection).

"Serious" = scanner recommendation in {REVIEW_NEEDED, NOT_RECOMMENDED, DO_NOT_USE}
(i.e. at least one HIGH/CRITICAL finding). This tests the static layer ONLY; it is
not a measure of the full skill, whose layer-2/3 reasoning is the load-bearing part.

Requirements: Python 3.6+, stdlib only. No network. Run: python3 test/run_eval.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
sys.path.insert(0, SKILL_DIR)
import scan  # noqa: E402

SERIOUS = {"REVIEW_NEEDED", "NOT_RECOMMENDED", "DO_NOT_USE"}

# Expected tier per malicious fixture:
#   serious      -> static layer MUST flag it serious (regression gate)
#   gap-warning  -> only WARNING-level expected; layer-2 needed (informational)
#   gap-missed   -> static blind spot expected; layer-2 needed (informational)
MALICIOUS = {
    "reverse-shell": "serious",
    "cred-theft": "serious",
    "instruction-override": "serious",
    "hidden-instructions": "serious",
    "context-manipulation": "serious",
    "rce-curl-bash": "gap-warning",
    "exfil-env": "gap-warning",
    "obfuscation": "gap-warning",
    "hardcoded-token": "gap-missed",
    "behavior-manipulation": "gap-missed",
}

# Expected outcome per benign fixture:
#   clean    -> must NOT be flagged serious (false-positive gate)
#   known-fp -> benign but expected to be flagged serious by the static layer;
#               only layer-2 reasoning clears it (informational)
BENIGN = {
    "formatter": "clean",
    "git-helper": "clean",
    "weather-client": "clean",
    "local-logging": "clean",
    "self-installer": "clean",
    "security-docs": "known-fp",
}


def assess(path):
    report = scan.build_report(path)
    rec = report["recommendation"]
    sev = report["severity_summary"]
    return {
        "recommendation": rec,
        "serious": rec in SERIOUS,
        "highcrit": sev["CRITICAL"] + sev["HIGH"],
        "warn": sev["WARNING"],
        "total": report["total_findings"],
    }


def fixture_path(kind, name):
    return os.path.join(HERE, "fixtures", kind, name)


def main():
    failures = []
    print("=" * 72)
    print("  skill-review static-layer eval  (scan.py vs labeled fixtures)")
    print("=" * 72)

    # ---- Malicious ----
    print("\nMALICIOUS fixtures (attack class -> static-layer outcome)\n")
    serious_expected = serious_caught = 0
    detected_any = 0
    for name, tier in sorted(MALICIOUS.items()):
        r = assess(fixture_path("malicious", name))
        outcome = (r["recommendation"] if r["serious"]
                   else ("WARNING-only" if r["warn"] else "not detected"))
        if r["serious"] or r["warn"]:
            detected_any += 1
        flag = "ok"
        if tier == "serious":
            serious_expected += 1
            if r["serious"]:
                serious_caught += 1
            else:
                flag = "FAIL (regression: should be serious)"
                failures.append(f"malicious/{name} not flagged serious ({outcome})")
        elif tier == "gap-warning" and not r["warn"] and not r["serious"]:
            flag = "note (worse than expected: not detected at all)"
        elif tier == "gap-missed" and r["serious"]:
            flag = "note (better than expected: caught serious)"
        print(f"  {name:<22} [{tier:<11}] -> {outcome:<14} "
              f"(H/C={r['highcrit']} W={r['warn']})  {flag}")

    # ---- Benign ----
    print("\nBENIGN fixtures (must stay non-serious unless a documented FP)\n")
    benign_clean = benign_total = 0
    false_positives = 0
    for name, expect in sorted(BENIGN.items()):
        r = assess(fixture_path("benign", name))
        if expect == "clean":
            benign_total += 1
            if r["serious"]:
                false_positives += 1
                flag = "FAIL (false positive)"
                failures.append(f"benign/{name} flagged serious ({r['recommendation']})")
            else:
                benign_clean += 1
                flag = "ok"
        else:  # known-fp
            flag = ("note (known FP, as expected)" if r["serious"]
                    else "note (known-FP case did NOT fire)")
        print(f"  {name:<22} [{expect:<8}] -> {r['recommendation']:<16} "
              f"(H/C={r['highcrit']} W={r['warn']})  {flag}")

    # ---- Summary ----
    print("\n" + "-" * 72)
    print(f"  Static recall (serious tier) : {serious_caught}/{serious_expected} "
          "expected-serious malicious fixtures caught")
    print(f"  Detected at any level        : {detected_any}/{len(MALICIOUS)} "
          "malicious fixtures (serious or warning)")
    print(f"  Benign precision             : {benign_clean}/{benign_total} "
          f"clean fixtures stayed non-serious ({false_positives} false positives)")
    print(f"  Known gaps needing layer 2   : "
          f"{sum(1 for t in MALICIOUS.values() if t.startswith('gap'))} malicious "
          "classes (warning-only or missed) + discussion-vs-attack FP")
    print("-" * 72)

    if failures:
        print(f"\nRESULT: FAIL ({len(failures)} regression(s))")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nRESULT: PASS (no regressions; gaps above are expected and documented)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
