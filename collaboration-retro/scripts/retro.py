"""Safe state, digest, decision, persistence, and scheduler support CLI."""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
import re
import shlex
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from common import atomic_write, iso_utc, load_json, parse_time, stable_id, write_json
from config import KINDS, load_config
from proposals import validate_proposals
from digest import render_digest


def paths(config: dict[str, Any]) -> tuple[Path, Path, Path]:
    output = Path(config["output"]["dir"])
    return (output, Path(config.get("state_path", output / "state.json")),
            Path(config.get("decisions_ledger", output / "decisions.jsonl")))


@contextmanager
def exclusive(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def init_state(config: dict[str, Any], force: bool = False) -> Path:
    output, state, _ = paths(config)
    output.mkdir(parents=True, exist_ok=True)
    if state.exists() and not force:
        raise ValueError(f"state already exists: {state} (use --force to reset)")
    write_json(state, {"schema_version": 1, "agent": config["agent"], "last_mined_at": iso_utc()})
    return state


def _memory_body(content: str) -> str:
    if content.startswith("---\n") and "\n---\n" in content[4:]:
        content = content.split("\n---\n", 1)[1]
    return " ".join(content.casefold().split())


def known_entries(config: dict[str, Any]) -> tuple[set[str], set[str]]:
    import glob
    _, _, ledger = paths(config)
    ids, texts = set(), set()
    if ledger.exists():
        for number, line in enumerate(ledger.read_text(encoding="utf-8").splitlines(), 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{ledger}:{number}: invalid ledger JSON") from exc
            ids.add(str(row.get("proposal_id", "")))
            if isinstance(row.get("normalized_text"), str):
                texts.add(row["normalized_text"])
    patterns = list(config.get("memory_index", []))
    patterns += [str(Path(target) / "**" / "*") for target in config["persistence_targets"].values()]
    for pattern in patterns:
        for filename in glob.glob(pattern, recursive=True):
            candidate = Path(filename)
            if candidate.is_file():
                content = candidate.read_text(encoding="utf-8", errors="replace")
                body = _memory_body(content)
                if body:
                    texts.add(body)
                ids.update(re.findall(r"\b(?:feedback|reference|rule|skill|script)-[0-9a-f]{16}\b", content))
    return ids, texts


def decision_topics(config: dict[str, Any]) -> list[dict[str, str]]:
    _, _, ledger = paths(config)
    topics = []
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if isinstance(row.get("topic_key"), str):
                topics.append({"topic_key": row["topic_key"], "kind": str(row.get("kind", "")),
                               "decision": str(row.get("decision", "")),
                               "normalized_text": str(row.get("normalized_text", ""))})
    return topics


def prepare_mine(config: dict[str, Any], until: str | None = None) -> Path:
    from extractor import extract
    output, state, _ = paths(config)
    current = load_json(state)
    since = current.get("last_mined_at")
    if current.get("agent") not in {None, config["agent"]} or not isinstance(since, str):
        raise ValueError(f"invalid state for agent {config['agent']}: {state}")
    now = iso_utc()
    if until is not None and parse_time(until) > parse_time(now):
        raise ValueError("--until must not be later than now")
    until = iso_utc(parse_time(until)) if until is not None else now
    result = extract(config, since, until)
    ids, texts = known_entries(config)
    result["dedup_context"] = {"proposal_ids": sorted(ids), "normalized_ideas": sorted(texts),
                               "decisions": decision_topics(config)}
    stamp = parse_time(until).strftime("%Y%m%dT%H%M%S.%fZ")
    raw_path = output / "work" / f"{stamp}-{config['agent']}-raw.json"
    write_json(raw_path, result)
    return raw_path


def normalize_proposals(raw: Any, config: dict[str, Any]) -> list[dict[str, Any]]:
    proposals = validate_proposals(raw)
    known_ids, known_texts = known_entries(config)
    result, seen = [], set()
    for proposal in proposals:
        normalized = " ".join(proposal["text"].casefold().split())
        if proposal["id"] not in known_ids and proposal["id"] not in seen and normalized not in known_texts:
            result.append(proposal); seen.add(proposal["id"])
    return result


def finalize_mine(config: dict[str, Any], raw_path: Path, proposal_path: Path) -> Path:
    raw = load_json(raw_path)
    if raw.get("agent") != config["agent"] or raw.get("format") != config["format"]:
        raise ValueError("raw extraction agent or format does not match selected config")
    window = raw.get("window")
    if not isinstance(window, dict):
        raise ValueError("raw extraction has no valid window")
    try:
        start, end = parse_time(window["since"]), parse_time(window["until"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("raw extraction has invalid timestamps") from exc
    if end < start:
        raise ValueError("raw extraction window is not monotonic")
    canonical = {"since": iso_utc(start), "until": iso_utc(end)}
    proposals = normalize_proposals(load_json(proposal_path), config)
    output, state, _ = paths(config)
    window_id = hashlib.sha256(f"{canonical['since']}\0{canonical['until']}".encode()).hexdigest()[:12]
    stamp = end.strftime('%Y%m%dT%H%M%S.%fZ')
    digest = output / "digests" / f"{stamp}-{window_id}-{config['agent']}.md"
    sidecar = digest.with_suffix(".json")
    rendered = render_digest(config, canonical, proposals)
    metadata = {"schema_version": 1, "agent": config["agent"], "format": config["format"],
                "window": canonical, "digest_sha256": hashlib.sha256(rendered.encode()).hexdigest(),
                "proposals": proposals}
    with exclusive(state.with_suffix(state.suffix + ".lock")):
        current = load_json(state)
        if current.get("agent") not in {None, config["agent"]} or current.get("last_mined_at") != canonical["since"]:
            raise ValueError("raw extraction window no longer matches current state")
        _write_or_match(sidecar, json.dumps(metadata, indent=2, ensure_ascii=False) + "\n")
        _write_or_match(digest, rendered)
        new_state = {"schema_version": 1, "agent": config["agent"],
                     "last_mined_at": canonical["until"], "last_digest": str(digest)}
        try:
            write_json(state, new_state)
        except BaseException:
            # A replace can complete before a directory-fsync error is reported.
            if not state.exists() or load_json(state) != new_state:
                raise
    return digest


def _write_or_match(path: Path, content: str) -> None:
    if path.exists():
        if path.read_text(encoding="utf-8") != content:
            raise ValueError(f"conflicting completed output exists: {path}")
    else:
        atomic_write(path, content)

def digest_proposals(path: Path, config: dict[str, Any] | None = None):
    from apply import digest_proposals as load_proposals
    return load_proposals(path, config)


def apply_digest(config: dict[str, Any], digest: Path) -> None:
    from apply import apply_digest as run_apply
    run_apply(config, digest, paths, known_entries, exclusive)


def install_schedule(config: dict[str, Any], dry_run: bool) -> Path:
    import plistlib
    schedule = config.get("scheduler", {})
    required = ("label", "weekday", "hour", "minute", "mine_command", "plist_path")
    if any(key not in schedule for key in required):
        raise ValueError("scheduler requires " + ", ".join(required))
    command = schedule["mine_command"]
    if not isinstance(command, list) or not command or not all(isinstance(part, str) and part for part in command):
        raise ValueError("scheduler.mine_command must be a non-empty argument array")
    runner = Path(__file__).resolve().with_name("schedule_runner.py")
    data = {"Label": schedule["label"], "ProgramArguments": [sys.executable, str(runner),
            "--config", config["config_path"], "--agent", config["agent"]], "RunAtLoad": False,
            "StartCalendarInterval": {"Weekday": schedule["weekday"], "Hour": schedule["hour"],
                                      "Minute": schedule["minute"]}}
    for source, plist_key in (("stdout_path", "StandardOutPath"), ("stderr_path", "StandardErrorPath")):
        if source in schedule:
            Path(schedule[source]).parent.mkdir(parents=True, exist_ok=True)
            data[plist_key] = schedule[source]
    payload, destination = plistlib.dumps(data).decode(), Path(schedule["plist_path"])
    if dry_run:
        print(payload, end="")
    else:
        atomic_write(destination, payload)
        print(f"installed {destination}; load with: launchctl load {shlex.quote(str(destination))}")
    return destination


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True); parser.add_argument("--agent")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init-state"); init.add_argument("--force", action="store_true")
    prepare = sub.add_parser("prepare-mine")
    prepare.add_argument("--until", help="end a smaller batch without resetting state")
    final = sub.add_parser("finalize-mine"); final.add_argument("--raw", required=True); final.add_argument("--proposals", required=True)
    apply = sub.add_parser("apply"); apply.add_argument("--digest", required=True)
    install = sub.add_parser("install-schedule"); install.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv); config = load_config(args.config, args.agent)
    if args.command == "init-state": print(init_state(config, args.force))
    elif args.command == "prepare-mine": print(prepare_mine(config, args.until))
    elif args.command == "finalize-mine": print(finalize_mine(config, Path(args.raw), Path(args.proposals)))
    elif args.command == "apply": apply_digest(config, Path(args.digest))
    else: install_schedule(config, args.dry_run)
    return 0

if __name__ == "__main__":
    try: raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"retro: error: {exc}", file=sys.stderr); raise SystemExit(2)
