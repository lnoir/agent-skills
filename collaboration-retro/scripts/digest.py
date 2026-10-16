"""Deterministic approval view shared by mine and apply."""
from __future__ import annotations
import json
from typing import Any


def _literal_block(value: str) -> list[str]:
    # JSON escaping is a bijection over the exact Unicode content and cannot
    # terminate Markdown indentation/fences.
    return ["    " + json.dumps(value, ensure_ascii=True)]

def render_digest(config: dict[str, Any], window: dict[str, str], proposals: list[dict[str, Any]]) -> str:
    lines = [f"# Collaboration retro — {window['until'][:10]}", "", f"Agent: `{config['agent']}`  ",
             f"Window: `{window['since']}` → `{window['until']}`", "",
             "Nothing below is approved. Run apply manually to accept or decline each proposal.", ""]
    if not proposals:
        lines += ["## No new proposals", "", "All candidates were irrelevant or previously decided/saved."]
    for number, item in enumerate(proposals, 1):
        lines += [f"## {number}. [{item['kind']}] {item['id']}", "", item["text"]]
        if item["rationale"]:
            lines += ["", f"Why: {item['rationale']}"]
        if item["evidence"]:
            lines += ["", "Evidence: " + ", ".join(item["evidence"])]
        # Prose layout alone is ambiguous (text can contain "Why:"/"Evidence:").
        # This single escaped JSON line binds every non-artifact proposal field.
        fields = {key: value for key, value in item.items() if key != "artifact"}
        lines += ["", "Proposal fields (JSON; escapes are exact):", "",
                  "    " + json.dumps(fields, sort_keys=True, ensure_ascii=True)]
        if item.get("artifact"):
            lines += ["", "Artifact bytes (JSON strings; escapes are exact):"]
            artifact = item["artifact"]
            files = artifact["files"] if item["kind"] == "skill" else {artifact["filename"]: artifact["content"]}
            for filename, content in files.items():
                lines += [f"", f"File: `{json.dumps(filename)}`", ""] + _literal_block(content)
        lines.append("")
    return "\n".join(lines)
