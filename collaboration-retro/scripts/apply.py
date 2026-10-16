"""Validated digest loading and locked, no-clobber application."""
from __future__ import annotations
import hashlib
import json
import os
import shutil
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from common import atomic_write, iso_utc, load_json
from proposals import validate_proposals
from digest import render_digest


def digest_metadata(config: dict[str, Any] | None, digest: Path) -> dict[str, Any]:
    content = digest.read_text(encoding="utf-8")
    metadata = load_json(digest.with_suffix(".json"))
    if not isinstance(metadata, dict):
        raise ValueError("digest metadata must be an object")
    if config is not None and (metadata.get("agent") != config["agent"] or
                               metadata.get("format") != config["format"]):
        raise ValueError("digest metadata does not match selected agent")
    if metadata.get("digest_sha256") != hashlib.sha256(content.encode()).hexdigest():
        raise ValueError("digest content does not match authoritative metadata")
    proposals = validate_proposals(metadata.get("proposals"))
    if proposals != metadata.get("proposals"):
        raise ValueError("digest metadata proposals are not canonical")
    window = metadata.get("window")
    if (not isinstance(metadata.get("agent"), str) or not isinstance(window, dict) or
            not all(isinstance(window.get(key), str) for key in ("since", "until"))):
        raise ValueError("digest metadata has invalid agent or window")
    if render_digest(metadata, window, proposals) != content:
        raise ValueError("digest content does not match validated proposals")
    metadata["proposals"] = proposals
    return metadata


def digest_proposals(path: Path, config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return digest_metadata(config, path)["proposals"]


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_file(destination: Path, content: str, executable: bool = False) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    stage = Path(temporary)
    published = False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if executable:
            stage.chmod(stage.stat().st_mode | 0o100)
        os.link(stage, destination)  # exclusive create: never replaces another operation's file
        published = True
        _fsync_dir(destination.parent)
        return destination
    except BaseException:
        if published:
            destination.unlink(missing_ok=True)
        raise
    finally:
        stage.unlink(missing_ok=True)


def _publish_skill(target: Path, proposal: dict[str, Any]) -> Path:
    """Publish a normal directory while the target lock prevents destination races."""
    target.mkdir(parents=True, exist_ok=True)
    destination = target / proposal["id"]
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"artifact already exists: {destination}")
    payload = Path(tempfile.mkdtemp(prefix=f".{proposal['id']}.", dir=target))
    published = False
    try:
        for name, content in proposal["artifact"]["files"].items():
            file = payload.joinpath(*name.split("/"))
            atomic_write(file, content)
        # There is no portable no-replace directory rename. The caller holds an
        # exclusive lock for this canonical target, and we checked before create.
        payload.rename(destination)
        published = True
        _fsync_dir(target)
        return destination
    except BaseException:
        if published:
            shutil.rmtree(destination, ignore_errors=True)
        elif payload.exists():
            shutil.rmtree(payload, ignore_errors=True)
        raise


def persist(config: dict[str, Any], proposal: dict[str, Any]) -> list[Path]:
    target = Path(config["persistence_targets"][proposal["kind"]])
    if proposal["kind"] == "skill":
        return [_publish_skill(target, proposal)]
    if proposal["kind"] == "script":
        artifact = proposal["artifact"]
        return [_publish_file(target / f"{proposal['id']}-{artifact['filename']}", artifact["content"], True)]
    destination = target / f"{proposal['id']}.md"
    body = (f"---\nid: {proposal['id']}\nkind: {proposal['kind']}\n"
            f"approved_at: {iso_utc()}\n---\n\n{proposal['text']}\n")
    return [_publish_file(destination, body)]


def rollback(created: list[Path]) -> None:
    """Remove only paths this transaction created; never touch pre-existing paths."""
    for destination in reversed(created):
        if destination.is_symlink() or destination.is_file():
            destination.unlink(missing_ok=True)
        elif destination.is_dir():
            shutil.rmtree(destination, ignore_errors=True)


def _ledger_records(ledger: Path, rows: list[dict[str, Any]]) -> bool:
    """Whether a failed write nevertheless left every decision recoverable."""
    try:
        existing = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    except (OSError, json.JSONDecodeError):
        return False
    expected = {(row["proposal_id"], row["decision"]) for row in rows}
    found = {(str(row.get("proposal_id")), str(row.get("decision")))
             for row in existing if isinstance(row, dict)}
    return expected <= found


def _target_lock_paths(config: dict[str, Any], selections: list[tuple[dict[str, Any], str]]) -> list[Path]:
    targets = {Path(config["persistence_targets"][item["kind"]]).resolve()
               for item, choice in selections if choice == "accept"}
    return [path / ".collaboration-retro.lock" for path in sorted(targets, key=str)]


def apply_digest(config: dict[str, Any], digest: Path, path_helper, known_loader, lock_factory) -> None:
    metadata = digest_metadata(config, digest)
    proposals = metadata["proposals"]
    print(render_digest(metadata, metadata["window"], proposals))
    selections = []
    for item in proposals:
        choice = input(f"{item['id']} [{item['kind']}] accept/decline/skip? ").strip().casefold()
        selections.append((item, choice if choice in {"accept", "decline"} else "skip"))
    _, _, ledger = path_helper(config)
    locks = [ledger.with_suffix(ledger.suffix + ".lock")] + _target_lock_paths(config, selections)
    # A common global order across ledger and canonical target locks avoids deadlock.
    locks = sorted(set(locks), key=lambda path: str(path.resolve()))
    with ExitStack() as stack:
        for lock in locks:
            stack.enter_context(lock_factory(lock))
        known, _ = known_loader(config)
        old = ledger.read_text(encoding="utf-8") if ledger.exists() else ""
        rows, created = [], []
        try:
            for item, choice in selections:
                if choice == "skip" or item["id"] in known:
                    continue
                files = persist(config, item) if choice == "accept" else []
                created.extend(files)
                rows.append({"timestamp": iso_utc(), "agent": config["agent"], "proposal_id": item["id"],
                             "topic_key": item["topic_key"], "kind": item["kind"],
                             "decision": "accepted" if files else "declined",
                             "normalized_text": " ".join(item["text"].casefold().split()),
                             "destinations": [str(path) for path in files], "digest": str(digest)})
                known.add(item["id"])
            if rows:
                atomic_write(ledger, old + "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
        except BaseException:
            # atomic_write can report a late fsync error after replace. Retain
            # artifacts only when the on-disk ledger proves every decision is
            # recoverable; otherwise roll back exactly this transaction's paths.
            if not _ledger_records(ledger, rows):
                rollback(created)
            raise
