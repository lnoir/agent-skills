"""Pure proposal schema validation shared by mine and apply."""
from __future__ import annotations
from typing import Any

from common import stable_id
from config import KINDS


def _portable_path(value: Any, label: str) -> str:
    """Return a strictly portable, relative POSIX path."""
    if (not isinstance(value, str) or not value or "\\" in value or "\x00" in value or
            len(value) >= 2 and value[1] == ":"):
        raise ValueError(f"{label}: invalid skill artifact file")
    # Split explicitly rather than using pathlib: proposals are portable POSIX
    # data even when the command happens to run on Windows.
    parts = value.split("/")
    if value.startswith("/") or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"{label}: invalid skill artifact file")
    return "/".join(parts)


def _artifact(kind: str, value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label}: {kind} requires an artifact object")
    if kind == "skill":
        files = value.get("files")
        if not isinstance(files, dict) or "SKILL.md" not in files or not files:
            raise ValueError(f"{label}: skill artifact requires files.SKILL.md")
        clean: dict[str, str] = {}
        for name, content in files.items():
            path = _portable_path(name, label)
            if path in clean or not isinstance(content, str) or not content:
                raise ValueError(f"{label}: invalid skill artifact file")
            clean[path] = content
        return {"files": clean}
    filename, content = value.get("filename"), value.get("content")
    if (not isinstance(filename, str) or not filename or filename in {".", ".."} or
            "/" in filename or "\\" in filename or "\x00" in filename or
            len(filename) >= 2 and filename[1] == ":" or
            not isinstance(content, str) or not content):
        raise ValueError(f"{label}: invalid script artifact")
    return {"filename": filename, "content": content}


def validate_proposal(item: Any, index: int) -> dict[str, Any]:
    label = f"proposal {index}"
    if not isinstance(item, dict) or item.get("kind") not in KINDS:
        raise ValueError(f"{label}: unknown kind")
    kind, text, topic = item["kind"], item.get("text"), item.get("topic_key")
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"{label}: text is required")
    if (not isinstance(topic, str) or len(topic) < 3 or len(topic) > 80 or
            not topic[0].isalnum() or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in topic)):
        raise ValueError(f"{label}: topic_key must be a lowercase slug")
    evidence, rationale = item.get("evidence", []), item.get("rationale", "")
    if not isinstance(evidence, list) or not all(isinstance(value, str) for value in evidence):
        raise ValueError(f"{label}: evidence must be an array of strings")
    if not isinstance(rationale, str):
        raise ValueError(f"{label}: rationale must be a string")
    result = {"id": stable_id(kind, topic), "kind": kind, "text": text.strip(),
              "topic_key": topic, "rationale": rationale.strip(), "evidence": evidence}
    if kind in {"skill", "script"}:
        result["artifact"] = _artifact(kind, item.get("artifact"), label)
    elif "artifact" in item:
        raise ValueError(f"{label}: artifact is only valid for skill/script")
    return result


def validate_proposals(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("proposal data must be a JSON array")
    return [validate_proposal(item, index) for index, item in enumerate(value, 1)]
