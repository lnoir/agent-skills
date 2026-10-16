"""Bounded conversation candidates, literal signals, and tool frequencies."""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from adapters import get_adapter
from common import iso_utc, parse_time, write_json
from extraction_context import (CANDIDATE_LIMIT, CONTEXT_TURNS, TEXT_LIMIT,
                                ConversationContext, evidence_turn)
from config import expand_globs, load_config

CORRECTION = re.compile(r"\b(no|don't|do not|instead|actually|wrong|incorrect|not what|stop)\b", re.I)
IMPROVEMENT = re.compile(r"\b(context|spec(?:ification)?|requirements?|technique|for reference|here(?:'s| is)|use this)\b", re.I)


def _in_window(timestamp: Any, since, until) -> bool:
    if not isinstance(timestamp, str):
        return False
    try:
        value = parse_time(timestamp)
    except ValueError:
        return False
    return since < value <= until


def extract(config: dict[str, Any], since: str, until: str) -> dict[str, Any]:
    start, end = parse_time(since), parse_time(until)
    if end < start:
        raise ValueError("--until must not precede --since")
    adapter = get_adapter(config["format"])
    lenses: dict[str, Any] = {"corrections": [], "repetition": [], "improvements": []}
    correction_occurrences: dict[str, dict[str, Any]] = {}
    command_counts: Counter[str] = Counter()
    workflow_counts: Counter[str] = Counter()
    sessions: set[str] = set()
    source_count = 0
    candidates: list[dict[str, Any]] = []
    shell_names = set(config["tool_semantics"]["shell"])
    workflow_names = set(config["tool_semantics"]["workflow"])
    for path in expand_globs(config["transcript_globs"]):
        source_count += 1
        context = ConversationContext(candidates)
        for event in adapter(path):
            if not _in_window(event["timestamp"], start, end):
                continue
            sessions.add(event["session_id"])
            if event["text"].strip():
                item = evidence_turn(path, event)
                context.add(item)
            if event["role"] == "user" and event["text"].strip():
                text = event["text"].strip()
                if CORRECTION.search(text):
                    lenses["corrections"].append(item)
                    key = " ".join(text.casefold().split())
                    repeated = correction_occurrences.setdefault(key, {"text": item["text"], "sessions": set(), "count": 0})
                    repeated["sessions"].add(event["session_id"])
                    repeated["count"] += 1
                if IMPROVEMENT.search(text):
                    lenses["improvements"].append(item)
            for tool in event["tools"]:
                name, data = tool["name"], tool["input"]
                if name in shell_names and isinstance(data, dict):
                    command = data.get("command")
                    if isinstance(command, str) and command.strip():
                        command_counts[command.strip()] += 1
                elif name in workflow_names:
                    if isinstance(data, dict):
                        value = data.get("skill") or data.get("name") or json.dumps(data, sort_keys=True)
                    else:
                        value = str(data)
                    workflow_counts[str(value)] += 1
    for repeated in correction_occurrences.values():
        if len(repeated["sessions"]) > 1:
            lenses["repetition"].append({"text": repeated["text"], "count": repeated["count"],
                                           "session_count": len(repeated["sessions"])})
    return {"schema_version": 1, "agent": config["agent"], "format": config["format"],
            "window": {"since": iso_utc(start), "until": iso_utc(end)},
            "sources_scanned": source_count, "sessions_seen": len(sessions), "lenses": lenses,
            "candidates": candidates, "context_limits": {"nearby_turns_each_side": CONTEXT_TURNS,
                "characters_per_turn": TEXT_LIMIT, "max_candidates": CANDIDATE_LIMIT},
            "repetition": {"commands": dict(command_counts.most_common()),
                           "workflows": dict(workflow_counts.most_common())}}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--agent")
    parser.add_argument("--since", required=True)
    parser.add_argument("--until", required=True)
    parser.add_argument("--output", help="atomic JSON output; otherwise stdout")
    args = parser.parse_args(argv)
    result = extract(load_config(args.config, args.agent), args.since, args.until)
    if args.output:
        write_json(Path(args.output).expanduser(), result)
    else:
        json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"extract: error: {exc}", file=sys.stderr)
        raise SystemExit(2)
