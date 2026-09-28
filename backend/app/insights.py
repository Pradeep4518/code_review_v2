"""Team-impact logic. Pure functions only (no FastAPI, no network) so it is easy to test.

Each part answers one real code-review pain point:

1. Reviews are slow            -> verdict(), minutes_saved()            (instant merge-readiness + time saved)
2. Same mistakes repeat        -> repeat_counts(), recurring()          (recurring-mistake detector)
3. Knowledge leaves with people-> playbook_markdown(), knowledge_stats()(rules keep owner + reason, exportable)
4. Reviews are inconsistent    -> scorecard(), consistency()            (same standards, same result)
5. AI tools are generic        -> compare_delta(), team_vs_general()    (what memory added over a generic AI)

Everything is computed from real stored reviews. The only estimate is `minutes_saved`, and its
assumptions are exposed through ASSUMPTIONS so the UI can show them.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from .topics import CATEGORIES

SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

# ---- assumptions behind the ONE estimated number (senior reviewer time saved) ----
BASE_REVIEW_MIN = 5  # minutes a senior would spend just reading a PR
PER_ISSUE_MIN = 5    # minutes to spot, write up and explain one issue
ASSUMPTIONS = {
    "base_review_minutes": BASE_REVIEW_MIN,
    "per_issue_minutes": PER_ISSUE_MIN,
    "note": "Estimate only: each distinct PR saves a senior reviewer roughly "
            f"{BASE_REVIEW_MIN} min of reading plus {PER_ISSUE_MIN} min per issue RepoMind caught.",
}


# --------------------------------------------------------------------------- helpers
def fingerprint(title: str) -> str:
    """Stable id for 'the same kind of mistake', tolerant of wording, hyphens and case."""
    return re.sub(r"[^a-z0-9]+", "", str(title).lower())


def code_hash(text: str) -> str:
    norm = "\n".join(line.rstrip() for line in str(text).strip().splitlines())
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]


def _brief(i: dict[str, Any]) -> dict[str, Any]:
    return {"title": i.get("title", ""), "severity": i.get("severity", "medium"), "line": i.get("line")}


def _match(a: list[dict[str, Any]], b: list[dict[str, Any]]):
    """One-to-one match of two issue lists (same kind of mistake, or same line)."""
    used: set[int] = set()
    matched, only_a = [], []
    for x in a:
        hit = None
        for j, y in enumerate(b):
            if j in used:
                continue
            same_title = fingerprint(x.get("title", "")) == fingerprint(y.get("title", ""))
            same_line = x.get("line") is not None and x.get("line") == y.get("line")
            if same_title or same_line:
                hit = j
                break
        if hit is None:
            only_a.append(x)
        else:
            used.add(hit)
            matched.append((x, b[hit]))
    only_b = [y for j, y in enumerate(b) if j not in used]
    return matched, only_a, only_b


# --------------------------------------------------------------------------- 1. speed
def verdict(issues: list[dict[str, Any]]) -> dict[str, str]:
    """Instant merge-readiness, so a developer does not wait for a human to say 'fix this first'."""
    sev = Counter(i.get("severity", "medium") for i in issues)
    if sev["critical"]:
        n = sev["critical"]
        return {"status": "blocked", "label": "Blocked", "reason": f"{n} critical issue{'s' if n != 1 else ''} to fix before merging"}
    blocking = sev["high"] + sev["medium"]
    if blocking:
        return {"status": "changes", "label": "Needs changes", "reason": f"{blocking} high/medium issue{'s' if blocking != 1 else ''} to address"}
    if issues:
        return {"status": "ready", "label": "Ready to merge", "reason": "Only low-severity notes"}
    return {"status": "ready", "label": "Ready to merge", "reason": "No issues found"}


def minutes_saved(n_issues: int) -> int:
    return BASE_REVIEW_MIN + PER_ISSUE_MIN * max(0, n_issues)


# --------------------------------------------------------------------------- 2. repeat mistakes
def repeat_counts(reviews: list[dict[str, Any]], current_hash: str) -> dict[str, int]:
    """fingerprint -> number of DIFFERENT earlier PRs (distinct code) where this mistake was flagged.

    Re-reviewing the same code does not inflate the count."""
    seen: dict[str, set[str]] = {}
    for r in reviews:
        h = r.get("code_hash") or code_hash(r.get("code_diff", ""))
        if h == current_hash:
            continue
        for i in r.get("issues", []):
            seen.setdefault(fingerprint(i.get("title", "")), set()).add(h)
    return {k: len(v) for k, v in seen.items()}


def annotate_repeats(issues: list[dict[str, Any]], counts: dict[str, int]) -> None:
    for i in issues:
        n = counts.get(fingerprint(i.get("title", "")), 0)
        i["seen_before"] = n
        # repeated 2+ times and no team rule covers it yet -> worth teaching
        i["suggest_rule"] = n >= 2 and not i.get("memory_ids")


def recurring(reviews: list[dict[str, Any]], min_prs: int = 2, limit: int = 8) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for r in reviews:
        h = r.get("code_hash") or code_hash(r.get("code_diff", ""))
        for i in r.get("issues", []):
            g = groups.setdefault(fingerprint(i.get("title", "")), {
                "title": i.get("title", ""), "severity": i.get("severity", "medium"), "hashes": set(), "covered": False, "last_seen": None})
            g["hashes"].add(h)
            g["covered"] = g["covered"] or bool(i.get("memory_ids"))
            if SEV_ORDER.get(i.get("severity", "medium"), 2) < SEV_ORDER.get(g["severity"], 2):
                g["severity"] = i["severity"]
            if not g["last_seen"] or (r.get("created_at") or "") > g["last_seen"]:
                g["last_seen"], g["title"] = r.get("created_at"), i.get("title", g["title"])
    rows = [{"title": g["title"], "severity": g["severity"], "prs": len(g["hashes"]), "covered_by_rule": g["covered"], "last_seen": g["last_seen"]}
            for g in groups.values() if len(g["hashes"]) >= min_prs]
    rows.sort(key=lambda x: (-x["prs"], SEV_ORDER.get(x["severity"], 2)))
    return rows[:limit]


# --------------------------------------------------------------------------- 3. knowledge stays
def knowledge_stats(memories: list[dict[str, Any]]) -> dict[str, Any]:
    owners = sorted({m["owner"] for m in memories if m.get("owner")})
    return {
        "rules": len(memories),
        "with_reason": sum(1 for m in memories if m.get("reason")),
        "with_owner": sum(1 for m in memories if m.get("owner")),
        "contributors": owners,
        "applied_rules": sum(1 for m in memories if m.get("times_applied", 0) > 0),
    }


def playbook_markdown(memories: list[dict[str, Any]], generated_at: str | None = None) -> str:
    """A plain-text team playbook. It outlives any one person on the team."""
    when = (generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds"))[:10]
    lines = [
        "# Team Engineering Playbook",
        f"_Exported from RepoMind on {when} · {len(memories)} rule{'s' if len(memories) != 1 else ''}_",
        "",
        "These are the rules this team has taught, with the reason behind each one, so the knowledge stays even when people move on.",
        "",
    ]
    if not memories:
        lines.append("_No rules have been taught yet._")
    for cat in CATEGORIES:
        rules = [m for m in memories if m.get("category") == cat]
        if not rules:
            continue
        lines += [f"## {cat} ({len(rules)})", ""]
        for n, m in enumerate(rules, 1):
            lines.append(f"{n}. **{m['text']}**")
            lines.append(f"   - Why: {m['reason']}" if m.get("reason") else "   - Why: _not recorded yet_")
            who = m.get("owner") or "unknown"
            when_taught = str(m.get("created_at") or "")[:10] or "unknown date"
            lines.append(f"   - Taught by: {who} · {when_taught} · applied in {m.get('times_applied', 0)} review(s)")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- 4. consistency
def scorecard(memories: list[dict[str, Any]], issues: list[dict[str, Any]]) -> dict[str, Any]:
    """The same checklist for every review: each recalled team rule is either violated or passed."""
    violated_ids = {mid for i in issues for mid in i.get("memory_ids", [])}
    items = []
    for m in memories:
        items.append({"id": m["id"], "label": m.get("label") or f"{m.get('category', 'Other')} rule", "category": m.get("category", "Other"),
                      "text": m["text"], "status": "violated" if m["id"] in violated_ids else "passed"})
    total = len(items)
    violated = sum(1 for x in items if x["status"] == "violated")
    return {"total": total, "violated": violated, "passed": total - violated,
            "score": round(100 * (total - violated) / total) if total else None, "items": items}


def consistency(previous: list[dict[str, Any]] | None, current: list[dict[str, Any]], previous_id: str | None = None) -> dict[str, Any]:
    """Compare this review with an earlier review of the SAME code, so different results are visible."""
    if previous is None:
        return {"status": "first_review", "previous_review_id": None, "missing": [], "added": []}
    _, only_prev, only_cur = _match(previous, current)
    return {"status": "differs" if (only_prev or only_cur) else "consistent", "previous_review_id": previous_id,
            "missing": [i.get("title", "") for i in only_prev], "added": [i.get("title", "") for i in only_cur]}


# --------------------------------------------------------------------------- 5. generic vs team-aware
def compare_delta(plain: list[dict[str, Any]], memory: list[dict[str, Any]]) -> dict[str, Any]:
    """What team memory added on top of what a generic AI reviewer found."""
    matched, only_plain, only_mem = _match(plain, memory)
    return {
        "shared": len(matched),
        "upgraded": sum(1 for _, m in matched if m.get("memory_ids")),  # generic finding, now tied to a team rule
        "team_only": [_brief(i) for i in only_mem if i.get("memory_ids")],
        "memory_extra_general": [_brief(i) for i in only_mem if not i.get("memory_ids")],
        "generic_only": [_brief(i) for i in only_plain],
    }


# --------------------------------------------------------------------------- dashboard
def _latest_per_pr(reviews: list[dict[str, Any]], memory_enabled: bool | None = None) -> list[dict[str, Any]]:
    """One review per distinct PR (the latest). With no filter, a memory-enabled review is preferred."""
    latest: dict[str, dict[str, Any]] = {}
    for r in reviews:
        if memory_enabled is not None and bool(r.get("memory_enabled")) != memory_enabled:
            continue
        h = r.get("code_hash") or code_hash(r.get("code_diff", ""))
        cur = latest.get(h)
        if cur is None or memory_enabled is not None or r.get("memory_enabled") or not cur.get("memory_enabled"):
            latest[h] = r
    return list(latest.values())


def build_impact(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    prs = _latest_per_pr(reviews)
    lat = [r["latency_ms"] for r in reviews if isinstance(r.get("latency_ms"), (int, float))]
    verdicts = Counter(verdict(r.get("issues", []))["status"] for r in prs)
    speed = {
        "reviews": len(reviews), "distinct_prs": len(prs),
        "avg_review_seconds": round(sum(lat) / len(lat) / 1000, 1) if lat else None,
        "issues_caught": sum(len(r.get("issues", [])) for r in prs),
        "blocked_prs": verdicts["blocked"], "ready_prs": verdicts["ready"],
        "est_minutes_saved": sum(minutes_saved(len(r.get("issues", []))) for r in prs),
    }
    rec = recurring(reviews)
    repeat = {"recurring": rec, "recurring_count": len(rec), "not_yet_a_rule": sum(1 for x in rec if not x["covered_by_rule"])}

    scored = [r["scorecard"]["score"] for r in reviews if r.get("memory_enabled") and (r.get("scorecard") or {}).get("score") is not None]
    cons = [r["consistency"]["status"] for r in reviews if (r.get("consistency") or {}).get("status") in ("consistent", "differs")]
    consistency_ = {
        "avg_compliance": round(sum(scored) / len(scored)) if scored else None, "scored_reviews": len(scored),
        "repeat_reviews": len(cons), "repeat_consistent": cons.count("consistent"),
    }

    mem_prs = _latest_per_pr(reviews, True)
    team = sum(1 for r in mem_prs for i in r.get("issues", []) if i.get("memory_ids"))
    general = sum(1 for r in mem_prs for i in r.get("issues", []) if not i.get("memory_ids"))
    generic = {"memory_reviews": len(mem_prs), "team_specific_findings": team, "general_findings": general}
    return {"speed": speed, "repeat": repeat, "consistency": consistency_, "generic": generic, "assumptions": ASSUMPTIONS}
