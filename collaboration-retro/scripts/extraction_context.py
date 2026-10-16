"""Bounded, same-session conversation excerpts for model review."""
from __future__ import annotations
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from common import stable_id

CONTEXT_TURNS = 2
TEXT_LIMIT = 4000
CANDIDATE_LIMIT = 2000


def evidence_turn(path: Path, event: dict[str, Any]) -> dict[str, Any]:
    text = event["text"].strip()
    return {"id": stable_id("event", f'{path}:{event["session_id"]}:{event["line"]}:{text}'),
            "source": str(path), "line": event["line"], "session_id": event["session_id"],
            "timestamp": event["timestamp"], "role": event["role"], "text": text[:TEXT_LIMIT],
            "text_truncated": len(text) > TEXT_LIMIT}


class ConversationContext:
    """Stream one source; only in-window textual turns may be supplied."""

    def __init__(self, candidates: list[dict[str, Any]]):
        self.candidates = candidates
        self.previous = defaultdict(lambda: deque(maxlen=CONTEXT_TURNS))
        self.pending = defaultdict(list)

    def add(self, turn: dict[str, Any]) -> None:
        session = turn["session_id"]
        pending = self.pending[session]
        for candidate in pending:
            candidate["after"].append(turn)
        self.pending[session] = [candidate for candidate in pending
                                 if len(candidate["after"]) < CONTEXT_TURNS]
        if turn["role"] == "user":
            if len(self.candidates) >= CANDIDATE_LIMIT:
                raise ValueError(f"extraction exceeds {CANDIDATE_LIMIT} user candidates; "
                                 "retry a shorter window before finalizing")
            candidate = {"event": turn, "before": list(self.previous[session]), "after": []}
            self.candidates.append(candidate)
            self.pending[session].append(candidate)
        self.previous[session].append(turn)
