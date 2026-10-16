"""Local transcript format adapter registry."""
from .claude_jsonl import iter_claude_code_jsonl

ADAPTERS = {"claude-code-jsonl": iter_claude_code_jsonl}


def get_adapter(name: str):
    try:
        return ADAPTERS[name]
    except KeyError as exc:
        supported = ", ".join(sorted(ADAPTERS))
        raise ValueError(f"unknown transcript format {name!r}; supported: {supported}") from exc
