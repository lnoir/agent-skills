#!/usr/bin/env python3
"""skill-review static pre-scanner.

Recursively scans a target directory (an agent skill / plugin / tool package)
for malicious-code and malicious-instruction patterns, then scores the result
using the weighting from the MaliciousAgentSkillsBench analyzer.

Usage:
    python3 scan.py <path-to-target-dir> [--json]

This is layer 1 of a 3-layer methodology (see SKILL.md). It is a coarse,
over-firing candidate generator, NOT a verdict. Treat every finding as a lead to
confirm during the intent-alignment reasoning step.

stdlib only; safe to run on a clean Python 3.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rules  # noqa: E402
import report as report_mod  # noqa: E402

# Directories never worth scanning.
SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "vendor", "dist", "build",
    "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache",
    ".idea", ".vscode", "target", ".pairing",
}
# Extensions that are binary / not worth scanning as text.
SKIP_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz",
    ".tar", ".tgz", ".bz2", ".7z", ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp3", ".mp4", ".mov", ".avi", ".wav", ".so", ".dylib", ".dll", ".class",
    ".pyc", ".o", ".a", ".bin", ".exe", ".wasm",
}
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MB: skip anything larger.
SNIPPET_MAX = 200


def _compile(rule):
    """Pre-compile a rule's patterns (case-insensitive, like NOVA's /i)."""
    compiled = []
    for pat in rule["patterns"]:
        try:
            compiled.append(re.compile(pat, re.IGNORECASE))
        except re.error:
            # A malformed pattern must never take the whole scan down.
            continue
    return compiled


_COMPILED = [(r, _compile(r)) for r in rules.ALL_RULES]


def iter_files(root):
    """Yield scannable file paths under root, pruning skip dirs/exts/binaries."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            ext = os.path.splitext(name)[1].lower()
            if ext in SKIP_EXTS:
                continue
            path = os.path.join(dirpath, name)
            try:
                if os.path.getsize(path) > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            yield path


def scan_file(path, root):
    """Return a list of finding dicts for one file. Never raises."""
    findings = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            content = fh.read()
    except (OSError, ValueError):
        return findings
    if "\x00" in content[:4096]:  # crude binary guard
        return findings

    rel = os.path.relpath(path, root)
    lines = content.split("\n")
    for i, line in enumerate(lines, 1):
        if len(line) > 5000:  # absurdly long line: minified/encoded blob
            line_to_test = line[:5000]
        else:
            line_to_test = line
        for rule, compiled in _COMPILED:
            for rx in compiled:
                if rx.search(line_to_test):
                    snippet = line.strip()
                    if len(snippet) > SNIPPET_MAX:
                        snippet = snippet[:SNIPPET_MAX] + "…"
                    findings.append({
                        "rule_id": rule["id"],
                        "category": rule["category"],
                        "name": rule["name"],
                        "severity": rule["severity"],
                        "file": rel,
                        "line": i,
                        "snippet": snippet,
                        "confidence": rule["confidence"],
                    })
                    break  # one hit per rule per line is enough
    return findings


def calculate_risk_score(findings):
    """Severity-weighted, confidence-scaled score normalized to 0-10.

    Mirrors analyzer.py: total = Σ(weight × confidence); the score saturates
    at 10 once roughly 20 max-weight findings accumulate.
    """
    total = 0.0
    for f in findings:
        weight = rules.SEVERITY_WEIGHTS.get(f["severity"], 1)
        total += weight * f["confidence"]
    max_score = 20 * 10 * 1.0
    return min((total / max_score) * 10, 10.0)


def risk_level(score):
    if score >= 8:
        return "CRITICAL"
    if score >= 6:
        return "HIGH"
    if score >= 4:
        return "MEDIUM"
    if score >= 2:
        return "LOW"
    return "SAFE"


def recommendation(findings):
    """Mirror analyzer.py.get_recommendation()."""
    crit = sum(1 for f in findings if f["severity"] == "CRITICAL")
    high = sum(1 for f in findings if f["severity"] == "HIGH")
    # Count-based triage prior, framed as "candidates to confirm" so it never
    # reads as a verdict that contradicts the score band (a single noisy HIGH
    # candidate can fire this while the magnitude band stays SAFE — by design).
    if crit > 0:
        return "DO_NOT_USE", "Critical candidate(s) — do not install without deep review."
    if high >= 3:
        return "NOT_RECOMMENDED", "Multiple high-severity candidates to confirm in layer 2 before trusting."
    if high > 0:
        return "REVIEW_NEEDED", "High-severity candidate(s) to confirm in layer 2 before trusting."
    return "SAFE", "No high-severity candidates. Still confirm intent alignment manually (layer 2)."


def build_report(target):
    findings = []
    files_scanned = 0
    for path in iter_files(target):
        files_scanned += 1
        findings.extend(scan_file(path, target))

    summary = {"CRITICAL": 0, "HIGH": 0, "WARNING": 0, "INFO": 0}
    cat_summary = {}
    for f in findings:
        if f["severity"] in summary:
            summary[f["severity"]] += 1
        cat_summary[f["category"]] = cat_summary.get(f["category"], 0) + 1

    score = calculate_risk_score(findings)
    rec, rec_text = recommendation(findings)
    report = {
        "target": target,
        "files_scanned": files_scanned,
        "total_findings": len(findings),
        "severity_summary": summary,
        "category_summary": cat_summary,
        "risk_score": round(score, 2),
        "risk_level": risk_level(score),
        "recommendation": rec,
        "recommendation_text": rec_text,
        "findings": findings,
    }
    report["plain"] = report_mod.build_plain_summary(report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Static pre-scan an agent skill for malicious patterns.")
    parser.add_argument("target", help="Path to the skill/plugin directory to scan.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args(argv)

    if not os.path.isdir(args.target):
        print(f"error: not a directory: {args.target}", file=sys.stderr)
        return 2

    report = build_report(args.target)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        report_mod.print_human(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
