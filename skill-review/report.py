"""Reporting layer for skill-review: plain-language summary + human output.

Kept separate from scan.py so the scanner stays focused on detection. The plain
summary is driven by finding severity/counts (the count-based recommendation),
never by the volume magnitude — so the headline never reads "SAFE" while real
findings exist, and a small-but-malicious skill is not mislabeled.
"""

_SEV_ORDER = {"CRITICAL": 0, "HIGH": 1, "WARNING": 2, "INFO": 3}

# Plain-English phrasing per category, for non-technical readers.
CATEGORY_PLAIN = {
    "file": "reads sensitive files (e.g. SSH keys, credentials, .env)",
    "network": "makes external network requests (can send data off your machine)",
    "command": "runs shell commands",
    "injection": "uses dynamic code execution (eval/exec) that can run arbitrary code",
    "dependency": "installs software packages",
    "instruction_override": "contains text that tries to override the AI's instructions",
    "context_manipulation": "contains text that tries to manipulate the AI "
                            "(hidden instructions, fake authority)",
    "encoding": "contains hidden or encoded text that could conceal instructions",
    "jailbreak": "contains text that tries to disable the AI's safety rules",
}


def build_plain_summary(report):
    """A non-technical headline + what-was-found + what-to-do."""
    rec = report["recommendation"]
    findings = report["findings"]
    s = report["severity_summary"]
    has_serious = s["CRITICAL"] > 0 or s["HIGH"] > 0

    if rec == "DO_NOT_USE":
        headline = "DO NOT INSTALL — critical malicious pattern(s) found"
        action = ("Do not install this. Have someone technical — or ask Claude — "
                  "explain each finding below before going any further.")
    elif rec == "NOT_RECOMMENDED":
        headline = "NOT RECOMMENDED — multiple serious warning signs"
        action = ("Do not install until every finding below is explained and you "
                  "understand why the skill needs to do it.")
    elif rec == "REVIEW_NEEDED":
        headline = "NEEDS REVIEW — serious warning sign(s) found"
        action = ("Get the finding(s) below explained before installing. If a "
                  "finding isn't justified by what the skill claims to do, don't install.")
    elif findings:  # only WARNING / INFO matched
        headline = "MINOR ITEMS FOUND — review before installing"
        action = ("No serious red flags, but check the lower-severity items below "
                  "against what the skill claims to do.")
    else:
        headline = "NO WARNING SIGNS FOUND"
        action = ("Nothing matched, but static scanning misses hidden or clever "
                  "attacks — this is not a guarantee. If unsure, ask Claude to review it.")

    # What was found, in plain words, worst categories first.
    cat_worst = {}
    for f in findings:
        rank = _SEV_ORDER.get(f["severity"], 9)
        if f["category"] not in cat_worst or rank < cat_worst[f["category"]]:
            cat_worst[f["category"]] = rank
    what_found = [
        CATEGORY_PLAIN.get(cat, cat)
        for cat, _ in sorted(cat_worst.items(), key=lambda kv: kv[1])
    ]
    return {"headline": headline, "what_found": what_found, "what_to_do": action,
            "is_serious": has_serious}


def print_human(report):
    import os
    p = report["plain"]
    bar = "=" * 64
    name = os.path.basename(os.path.normpath(report["target"])) or report["target"]
    print(f"\n{bar}")
    print(f"  skill-review  —  {name}")
    print(bar)
    print(f"\n  VERDICT: {p['headline']}")

    if p["what_found"]:
        print("\n  What was found — this skill:")
        for phrase in p["what_found"]:
            print(f"    • {phrase}")
    print(f"\n  What to do:\n    {p['what_to_do']}")

    # Technical detail, demoted below the plain summary.
    s = report["severity_summary"]
    print(f"\n  --- technical detail ---")
    print(f"  Files scanned : {report['files_scanned']}")
    print(f"  Findings      : {report['total_findings']} "
          f"(CRITICAL {s['CRITICAL']}, HIGH {s['HIGH']}, "
          f"WARNING {s['WARNING']}, INFO {s['INFO']})")
    print(f"  Magnitude     : {report['risk_score']}/10 ({report['risk_level']}) "
          f"— volume metric only; reads low for small skills, so it does NOT")
    print(f"                  drive the verdict above. Trust the findings, not this number.")

    if not report["findings"]:
        print("\n  No pattern matches. A clean pre-scan is necessary, not "
              "sufficient —\n  still do the intent-alignment review (see SKILL.md).")
        print(bar)
        return

    print("\n  Findings (highest severity first):")
    ordered = sorted(
        report["findings"],
        key=lambda f: (_SEV_ORDER.get(f["severity"], 9), f["file"], f["line"]),
    )
    for f in ordered:
        print(f"  [{f['severity']:<8}] {f['name']}  ({f['file']}:{f['line']})")
        print(f"             rule {f['rule_id']} <{f['category']}>  conf {f['confidence']}")
        print(f"             > {f['snippet']}")
    print("\n  Note: this scan over-fires by design. Confirm each finding against\n"
          "  what the skill claims to do before acting (SKILL.md, layer 2).")
    print(bar)
