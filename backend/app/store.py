"""Small JSON-file store for local application state.

Holds: review history, analytics events, per-issue feedback, memory usage counters,
and the LocalMemoryProvider's memories. Nothing here is ever labelled as Hindsight.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


DEFAULT_STATE: dict[str, Any] = {
    "reviews": [],
    "events": [],
    "feedback": [],
    "usage": {},
    "local_memories": [],
    "rule_meta": {},  # normalised rule text -> {"owner", "reason"}
    "rule_state": {},  # first-seen memory id -> lifecycle record (status, edits, history); see lifecycle.py
    "deleted_rules": [],  # tombstones: {"ids": [...], "at", "by", "text"} - hides rules the provider could not delete
}


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self._state: dict[str, Any] | None = None

    def _load(self) -> dict[str, Any]:
        if self._state is None:
            try:
                data = json.loads(self.path.read_text("utf-8"))
                self._state = {**json.loads(json.dumps(DEFAULT_STATE)), **data}
            except (FileNotFoundError, ValueError):
                self._state = json.loads(json.dumps(DEFAULT_STATE))
        return self._state

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, indent=1), "utf-8")
        os.replace(tmp, self.path)

    def read(self, fn: Callable[[dict[str, Any]], Any]) -> Any:
        with self.lock:
            return fn(self._load())

    def write(self, fn: Callable[[dict[str, Any]], Any]) -> Any:
        with self.lock:
            result = fn(self._load())
            self._save()
            return result

    # ---- domain helpers -------------------------------------------------
    def add_event(self, kind: str, **data: Any) -> None:
        self.write(lambda s: s["events"].append({"type": kind, "at": now_iso(), **data}))

    def add_review(self, review: dict[str, Any]) -> None:
        self.write(lambda s: s["reviews"].append(review))

    def get_review(self, review_id: str) -> dict[str, Any] | None:
        return self.read(lambda s: next((r for r in s["reviews"] if r["review_id"] == review_id), None))

    def record_usage(self, memory_ids: list[str], review_id: str) -> None:
        def fn(s: dict[str, Any]) -> None:
            for mid in memory_ids:
                u = s["usage"].setdefault(mid, {"count": 0, "last_used": None, "review_ids": []})
                u["count"] += 1
                u["last_used"] = now_iso()
                u["review_ids"].append(review_id)

        self.write(fn)

    def usage_for(self, memory_id: str) -> dict[str, Any]:
        return self.read(lambda s: dict(s["usage"].get(memory_id, {"count": 0, "last_used": None, "review_ids": []})))

    def add_feedback(self, fb: dict[str, Any]) -> None:
        self.write(lambda s: s["feedback"].append(fb))

    def set_rule_meta(self, key: str, meta: dict[str, Any]) -> None:
        self.write(lambda s: s["rule_meta"].__setitem__(key, {**s["rule_meta"].get(key, {}), **meta}))

    def get_rule_meta(self, key: str) -> dict[str, Any]:
        return self.read(lambda s: dict(s["rule_meta"].get(key, {})))

    def reviews_snapshot(self) -> list[dict[str, Any]]:
        return self.read(lambda s: list(s["reviews"]))

    def reset(self) -> None:
        def fn(s: dict[str, Any]) -> None:
            for k, v in json.loads(json.dumps(DEFAULT_STATE)).items():
                s[k] = v

        self.write(fn)
