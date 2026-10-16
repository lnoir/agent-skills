"""Configuration loading, validation, and schema-aware path expansion."""
from __future__ import annotations
import copy
import glob
import json
from pathlib import Path
from typing import Any

KINDS = {"feedback", "reference", "rule", "skill", "script"}
REQUIRED = ("transcript_globs", "format", "tool_semantics", "persistence_targets", "output")


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _is_path_field(parts: tuple[str, ...]) -> bool:
    return (parts == ("output", "dir") or
            len(parts) == 1 and parts[0] in {"state_path", "decisions_ledger"} or
            len(parts) == 2 and parts[0] == "scheduler" and parts[1] in {"plist_path", "stdout_path", "stderr_path"} or
            len(parts) == 2 and parts[0] == "persistence_targets" and parts[1] in KINDS or
            bool(parts) and parts[-1] in {"transcript_globs", "memory_index"})


def _expand(value: Any, agent: str, config_dir: Path, parts: tuple[str, ...] = ()) -> Any:
    if isinstance(value, dict):
        return {key: _expand(item, agent, config_dir, parts + (key,)) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand(item, agent, config_dir, parts) for item in value]
    if not isinstance(value, str):
        return value
    substituted = value.replace("{agent}", agent)
    if not _is_path_field(parts):
        return substituted
    expanded = Path(substituted).expanduser()
    return str(expanded if expanded.is_absolute() else config_dir / expanded)


def _string_list(value: Any, name: str) -> None:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"{name} must be an array of non-empty strings")


def load_config(path: str | Path, agent: str | None = None) -> dict[str, Any]:
    config_path = Path(path).expanduser().resolve()
    with config_path.open(encoding="utf-8") as handle:
        root = json.load(handle)
    if not isinstance(root, dict):
        raise ValueError("config root must be an object")
    defaults = root.get("defaults", {})
    if not isinstance(defaults, dict):
        raise ValueError("defaults must be an object")
    selected = agent or root.get("default_agent")
    agents = root.get("agents", {})
    if not isinstance(agents, dict) or not selected or selected not in agents:
        names = sorted(agents) if isinstance(agents, dict) else []
        raise ValueError(f"unknown agent {selected!r}; configured: {', '.join(names)}")
    if not isinstance(agents[selected], dict):
        raise ValueError(f"agent {selected!r} must be an object")
    resolved = deep_merge(defaults, agents[selected])
    missing = [key for key in REQUIRED if key not in resolved]
    if missing:
        raise ValueError(f"agent {selected!r} missing config: {', '.join(missing)}")
    resolved = _expand(resolved, selected, config_path.parent)
    output = resolved["output"]
    if not isinstance(output, dict) or not isinstance(output.get("dir"), str) or not output["dir"]:
        raise ValueError("output.dir must be a non-empty string")
    if not isinstance(resolved.get("format"), str) or not resolved["format"]:
        raise ValueError("format must be a non-empty string")
    _string_list(resolved["transcript_globs"], "transcript_globs")
    semantics = resolved["tool_semantics"]
    if not isinstance(semantics, dict):
        raise ValueError("tool_semantics must be an object")
    _string_list(semantics.get("shell"), "tool_semantics.shell")
    _string_list(semantics.get("workflow"), "tool_semantics.workflow")
    targets = resolved["persistence_targets"]
    if not isinstance(targets, dict):
        raise ValueError("persistence_targets must be an object")
    for kind in KINDS:
        if not isinstance(targets.get(kind), str) or not targets[kind]:
            raise ValueError(f"persistence_targets.{kind} must be a non-empty string")
    resolved.update(agent=selected, config_path=str(config_path))
    return resolved


def expand_globs(patterns: list[str]) -> list[Path]:
    paths: set[Path] = set()
    for pattern in patterns:
        paths.update(Path(item) for item in glob.glob(pattern, recursive=True))
    return sorted(path for path in paths if path.is_file())
