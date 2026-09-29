"""Rule lifecycle: edit, retire, supersede, delete, and "newest rule wins".

The memory providers (Hindsight / local) only know how to store and recall rules. Everything about how a
rule *changes over time* lives in a small ledger in the local store (``rule_state``), layered on top of
whichever provider is active. That keeps one behaviour for both providers and means a retired rule can be
restored, and a superseded rule keeps its history.

Ledger record (keyed by the first memory id it was seen under)::

    {ids, keys, status: active|retired|superseded, text?, category?, reason?,
     effective_at, updated_at, updated_by, retired_at, retired_by, retire_reason,
     superseded_by, supersedes, history: [{at, by, action, detail}]}
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .store import now_iso
from .topics import memory_topics

ACTIVE, RETIRED, SUPERSEDED = "active", "retired", "superseded"
HISTORY_CAP = 40


def normalize(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def parse_ts(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- ledger access (call inside store.read/write)
def find_state(rules: dict[str, dict[str, Any]], m: dict[str, Any]) -> dict[str, Any] | None:
    key = normalize(m["text"])
    for st in rules.values():
        if m["id"] in st["ids"] or key in st["keys"]:
            return st
    return None


def get_or_create(rules: dict[str, dict[str, Any]], m: dict[str, Any]) -> dict[str, Any]:
    st = find_state(rules, m)
    if st is None:
        st = {"ids": [m["id"]], "keys": [normalize(m["text"])], "status": ACTIVE, "history": []}
        rules[m["id"]] = st
    elif m["id"] not in st["ids"]:
        st["ids"].append(m["id"])
    return st


def log(st: dict[str, Any], by: str, action: str, detail: str = "") -> None:
    st["history"].append({"at": now_iso(), "by": by or "Unknown", "action": action, "detail": detail})
    del st["history"][:-HISTORY_CAP]


def is_deleted(tombstones: list[dict[str, Any]], m: dict[str, Any]) -> bool:
    ids = {m["id"], m.get("document_id")} - {None}
    return any(ids & set(t["ids"]) for t in tombstones)


# ---------------------------------------------------------------- overlay
def overlay(m: dict[str, Any], st: dict[str, Any] | None) -> dict[str, Any]:
    """Return the memory as the team should see it now: edits applied, lifecycle fields attached."""
    out = dict(m)
    st = st or {}
    for f in ("text", "category", "reason"):
        if st.get(f):
            out[f] = st[f]
    if st.get("text"):
        out["topics"] = memory_topics(out["text"])
        out["label"] = f"{out['category']} rule"
    out["status"] = st.get("status", ACTIVE)
    out["effective_at"] = st.get("effective_at") or m.get("created_at")
    out["given_by"] = m.get("owner") or m.get("source") or ""
    out["given_at"] = m.get("created_at")
    out["edited"] = bool(st.get("text"))
    for f in ("updated_at", "updated_by", "retired_at", "retired_by", "retire_reason", "superseded_by", "supersedes"):
        out[f] = st.get(f)
    out["history"] = list(st.get("history", []))
    return out


# ---------------------------------------------------------------- newest rule wins
def _short(text: str, n: int = 90) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def resolve_by_recency(conflicts: list[dict[str, Any]], memories: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], set[str]]:
    """Decide conflicts between two rules by date. Returns (conflicts with resolution info, ids to set aside).

    Only conflicts where both rules have a usable and *different* date are resolved; anything else is left
    open so the reviewer keeps surfacing it instead of guessing. A rule that has itself been set aside by a
    newer rule cannot then override an older one (decisions are applied newest-winner first).
    """
    by_id = {m["id"]: m for m in memories}
    decided: list[tuple[datetime, dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    out: list[dict[str, Any]] = []
    for c in conflicts:
        a, b = (by_id.get(i) for i in c["memory_ids"][:2])
        ta, tb = (parse_ts(x.get("effective_at")) if x else None for x in (a, b))
        if not (a and b and ta and tb) or ta == tb:
            out.append(c)
            continue
        win, lose = (a, b) if ta > tb else (b, a)
        decided.append((max(ta, tb), c, win, lose))
    dropped: set[str] = set()
    for _, c, win, lose in sorted(decided, key=lambda d: d[0], reverse=True):
        if win["id"] in dropped:
            continue  # the "winner" is itself out of play, so there is nothing left to disagree with
        dropped.add(lose["id"])
        out.append({
            **c,
            "resolution": "newest_wins",
            "winner_id": win["id"],
            "loser_id": lose["id"],
            "explanation": c["explanation"].replace(
                "RepoMind will not silently pick one — decide whether B is an approved exception to A.", "").rstrip()
            + f" Newest wins: “{_short(win['text'])}” is newer than “{_short(lose['text'])}”, so RepoMind applied the newer rule "
              f"and set the older one aside for this review. If that is wrong, edit or retire either rule in the Memory Bank.",
        })
    return out, dropped
