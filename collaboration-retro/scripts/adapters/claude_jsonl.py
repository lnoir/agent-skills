"""Adapter for Claude Code's JSONL session records."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator


def _text_and_tools(content: Any) -> tuple[str, list[dict[str, Any]]]:
    if isinstance(content, str):
        return content, []
    text: list[str] = []
    tools: list[dict[str, Any]] = []
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text" and isinstance(part.get("text"), str):
                text.append(part["text"])
            elif part.get("type") == "tool_use":
                tools.append({"name": part.get("name", ""), "input": part.get("input", {})})
    return "\n".join(text), tools


def iter_claude_code_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSONL: {exc.msg}") from exc
            message = row.get("message", {})
            role = message.get("role") or row.get("type")
            if role not in {"user", "assistant"}:
                continue
            text, tools = _text_and_tools(message.get("content", row.get("content", "")))
            yield {
                "role": role,
                "timestamp": row.get("timestamp"),
                "session_id": row.get("sessionId") or row.get("session_id") or path.stem,
                "text": text,
                "tools": tools,
                "line": line_number,
            }
