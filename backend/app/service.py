"""Orchestration: memory manager (Hindsight vs fallback), review pipeline, teach, feedback, analytics, DNA."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from . import insights, lifecycle, llm, reviewer
from .config import Settings
from .memory import HindsightMemoryProvider, LocalMemoryProvider, MemoryProviderError, normalize
from .models import EditRuleRequest, FeedbackRequest, RestoreRequest, RetireRequest, ReviewRequest, SupersedeRequest, TeachRequest
from .store import Store, now_iso
from .topics import CATEGORIES, TOPIC_PHRASES, detect_tech, memory_topics

log = logging.getLogger("repomind")

SEED_RULES: list[dict[str, str]] = [
    {"key": "seed-database-repository", "reason": "Keeps every SQL statement in one reviewed place, so injection fixes and query changes happen once.", "category": "Database", "rule": "All database access must go through repository/db.py. Application code must never build or execute SQL itself."},
    {"key": "seed-logging-structured", "reason": "Structured logs can be searched and alerted on; scattered print() output cannot.", "category": "Logging", "rule": "Use structured logging via the shared structlog logger. Never use print() or console.log for diagnostics."},
    {"key": "seed-security-sql-timeout", "reason": "Parameterized queries stop SQL injection, and the timeout stops one slow query from taking the service down.", "category": "Security", "rule": "Every SQL statement must use parameterized queries and run with a 5-second statement timeout."},
    {"key": "seed-security-sensitive-logging", "reason": "Log files are widely readable and long-lived, so leaked credentials there are hard to contain.", "category": "Security", "rule": "Never log authorization headers, tokens, or passwords."},
    {"key": "seed-api-contract", "reason": "Versioned paths and response models let clients depend on the API without breaking when it changes.", "category": "API", "rule": "Every API endpoint must be versioned under /api/v1 and declare a Pydantic response model."},
    {"key": "seed-security-authentication", "reason": "Trusting a client-supplied user_id lets any user act as any other user.", "category": "Security", "rule": "Protected endpoints must depend on get_current_user (JWT middleware) and never trust a user_id supplied by the client."},
    {"key": "seed-testing-pytest", "reason": "A failing test is the cheapest way to catch a regression before a reviewer or a customer does.", "category": "Testing", "rule": "Every behavior change ships with a pytest test; new endpoints need at least one happy-path and one failure-path test."},
    {"key": "seed-other-python-typing", "reason": "Strict typing catches whole classes of bugs before the code runs.", "category": "Other", "rule": "All Python functions must have full type annotations; the codebase runs mypy in strict mode."},
    {"key": "seed-architecture-error-handling", "reason": "Unhandled async failures surface as silent hangs or vague 500s; one AppError type keeps failures consistent.", "category": "Architecture", "rule": "Async I/O calls must be wrapped in try/except (or try/catch in JS) and surface failures through the shared AppError type."},
]


class MemoryManager:
    """Chooses the real Hindsight provider when it is reachable, otherwise the labelled local fallback."""

    def __init__(self, settings: Settings, store: Store, hindsight: HindsightMemoryProvider | None = None):
        self.settings = settings
        self.local = LocalMemoryProvider(store)
        self.hindsight = hindsight if hindsight is not None else (HindsightMemoryProvider(settings) if settings.hindsight_configured else None)
        self._status: tuple[float, dict[str, Any]] | None = None

    async def status(self, force: bool = False) -> dict[str, Any]:
        if self.hindsight is None:
            return {"connected": False, "configured": False, "error": None, "latency_ms": None}
        if not force and self._status and time.time() - self._status[0] < 30:
            return self._status[1]
        t0 = time.perf_counter()
        try:
            await self.hindsight.ping()
            st = {"connected": True, "configured": True, "error": None, "latency_ms": int((time.perf_counter() - t0) * 1000)}
        except MemoryProviderError as e:
            st = {"connected": False, "configured": True, "error": str(e), "latency_ms": None}
        self._status = (time.time(), st)
        return st

    def mark_failed(self, err: str) -> None:
        self._status = (time.time(), {"connected": False, "configured": True, "error": err, "latency_ms": None})

    async def active(self) -> tuple[Any, str | None]:
        st = await self.status()
        if st["connected"]:
            return self.hindsight, None
        return self.local, st["error"]


class Service:
    def __init__(self, settings: Settings, store: Store, hindsight: HindsightMemoryProvider | None = None):
        self.settings = settings
        self.store = store
        self.mm = MemoryManager(settings, store, hindsight)
        self.groq_error: str | None = None

    # ---------------------------------------------------------------- memory helpers
    def _with_usage(self, m: dict[str, Any]) -> dict[str, Any]:
        u = self.store.usage_for(m["id"])
        m = {k: v for k, v in m.items() if k != "topics"}
        meta = self.store.get_rule_meta(normalize(m["text"]))
        m["owner"] = m.get("owner") or meta.get("owner", "")
        m["reason"] = m.get("reason") or meta.get("reason", "")
        m["times_applied"] = u["count"]
        m["last_used"] = u["last_used"]
        m["related_reviews"] = self._related(u["review_ids"])
        return m

    def _related(self, review_ids: list[str]) -> list[dict[str, str]]:
        out, seen = [], set()
        for rid in reversed(review_ids):
            if rid in seen:
                continue
            seen.add(rid)
            r = self.store.get_review(rid)
            if r:
                out.append({"review_id": rid, "pr_title": r["pr_title"]})
            if len(out) >= 5:
                break
        return out

    async def _retain(self, rules: list[dict[str, str]]) -> tuple[Any, list[dict[str, Any]], str | None]:
        prov, mems, warn = await self._retain_raw(rules)
        for r, m in zip(rules, mems):  # keep who/why even if the provider drops the metadata
            meta = {k: r[k] for k in ("owner", "reason") if r.get(k)}
            if meta:
                self.store.set_rule_meta(normalize(m["text"]), meta)
        return prov, mems, warn

    async def _retain_raw(self, rules: list[dict[str, str]]) -> tuple[Any, list[dict[str, Any]], str | None]:
        prov, why = await self.mm.active()
        warn = f"Hindsight unavailable ({why}); saved to demo memory instead." if why and self.mm.hindsight else None
        try:
            return prov, await prov.retain(rules), warn
        except MemoryProviderError as e:
            if prov is self.mm.local:
                raise
            self.mm.mark_failed(str(e))
            return self.mm.local, await self.mm.local.retain(rules), f"Hindsight retain failed ({e}); saved to demo memory instead."

    # ---------------------------------------------------------------- rule lifecycle helpers
    def _apply_ledger(self, mems: list[dict[str, Any]], include_inactive: bool = False) -> list[dict[str, Any]]:
        """Drop deleted rules and overlay edits / status from the lifecycle ledger onto provider memories."""
        def fn(s: dict[str, Any]) -> list[dict[str, Any]]:
            out = []
            for m in mems:
                if lifecycle.is_deleted(s["deleted_rules"], m):
                    continue
                d = lifecycle.overlay(m, lifecycle.find_state(s["rule_state"], m))
                if include_inactive or d["status"] == lifecycle.ACTIVE:
                    out.append(d)
            return out

        return self.store.read(fn)

    def _hidden_count(self) -> int:
        return self.store.read(lambda s: sum(1 for st in s["rule_state"].values() if st["status"] != lifecycle.ACTIVE) + len(s["deleted_rules"]))

    async def _raw_list(self) -> tuple[Any, list[dict[str, Any]]]:
        prov, _ = await self.mm.active()
        try:
            return prov, await prov.list()
        except MemoryProviderError as e:
            if prov is self.mm.local:
                raise
            self.mm.mark_failed(str(e))
            return self.mm.local, await self.mm.local.list()

    async def _find(self, mem_id: str) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
        prov, raw = await self._raw_list()
        visible = self._apply_ledger(raw, include_inactive=True)
        ok = {m["id"] for m in visible}
        m = next((x for x in raw if x["id"] == mem_id and x["id"] in ok), None)
        if not m:
            raise KeyError("memory not found")
        return prov, m, raw

    def _view(self, m: dict[str, Any]) -> dict[str, Any]:
        """One memory as the UI shows it (edits + lifecycle + usage)."""
        return self._with_usage(self._apply_ledger([m], include_inactive=True)[0])

    @staticmethod
    def _who(actor: str) -> str:
        return (actor or "").strip() or "Unknown"

    # ---------------------------------------------------------------- health
    async def health(self) -> dict[str, Any]:
        st = await self.mm.status()
        prov, _ = await self.mm.active()
        try:
            raw = await prov.list()
            base = await prov.total() if prov.name == "hindsight" else len(raw)
            count = base - (len(raw) - len(self._apply_ledger(raw)))  # retired / replaced / deleted rules do not count
        except MemoryProviderError:
            count = 0
        groq = "NOT CONFIGURED" if not self.settings.groq_configured else ("DEGRADED" if self.groq_error else "READY")
        return {
            "status": "ok",
            "memory_mode": "HINDSIGHT CONNECTED" if prov.name == "hindsight" else "DEMO MEMORY MODE",
            "hindsight": {"configured": st["configured"], "connected": st["connected"], "bank_id": self.settings.hindsight_bank_id if st["configured"] else None, "error": st["error"], "latency_ms": st["latency_ms"]},
            "groq": {"configured": self.settings.groq_configured, "model": self.settings.groq_model, "status": groq, "last_error": self.groq_error},
            "memory_count": count,
            "review_engine": "groq" if self.settings.groq_configured and not self.groq_error else "local-deterministic",
        }

    # ---------------------------------------------------------------- review
    async def review(self, req: ReviewRequest) -> dict[str, Any]:
        t_start = time.perf_counter()
        warnings: list[str] = []
        parsed = reviewer.parse_diff(req.code_diff)
        memory_enabled = not req.bypass_memory
        memories: list[dict[str, Any]] = []
        provider_name = "none"
        if memory_enabled:
            topics = reviewer.detect_topics(parsed, req.review_mode)
            query = self._recall_query(req, topics, parsed)
            prov, why = await self.mm.active()
            if why and self.mm.hindsight:
                warnings.append(f"Hindsight unavailable ({why}); using demo memory.")
            limit = min(8 + self._hidden_count(), 30)  # retired rules must not eat recall slots
            try:
                memories = await prov.recall(query, topics, limit=limit)
            except MemoryProviderError as e:
                self.mm.mark_failed(str(e))
                warnings.append(f"Hindsight recall failed ({e}); using demo memory.")
                prov = self.mm.local
                memories = await prov.recall(query, topics, limit=limit)
            provider_name = prov.name
            memories = self._apply_ledger(memories)[:8]  # retired / replaced / deleted rules never reach the reviewer

        # Newest rule wins: when two recalled rules disagree and one is clearly newer, the reviewer only sees the newer one.
        recalled = memories
        resolved: list[dict[str, Any]] = []
        set_aside: set[str] = set()
        if memory_enabled and len(memories) > 1:
            probe = [{"key": f["key"]} for f in reviewer.run_detectors(parsed)]
            found, set_aside = lifecycle.resolve_by_recency(reviewer.detect_conflicts(memories, probe), memories)
            resolved = [c for c in found if c.get("resolution")]
            memories = [m for m in memories if m["id"] not in set_aside]

        result, provider, groq_ms, model = await self._run_reviewer(req, parsed, memories, memory_enabled, warnings)
        issues = result["issues"]
        prior = self.store.reviews_snapshot()  # earlier reviews, before this one is stored
        chash = insights.code_hash(req.code_diff)
        insights.annotate_repeats(issues, insights.repeat_counts(prior, chash))  # feature 2: repeat mistakes
        prev = next((r for r in reversed(prior) if (r.get("code_hash") or insights.code_hash(r.get("code_diff", ""))) == chash
                     and r.get("memory_enabled") == memory_enabled and r.get("review_mode") == req.review_mode), None)
        conflicts = reviewer.detect_conflicts(memories, issues) if memory_enabled else []
        for c in result.get("conflicts", []):
            if not any(set(c["memory_ids"]) == set(x["memory_ids"]) for x in conflicts):
                conflicts.append(c)
        for i in issues:
            i["conflict"] = any(set(c["memory_ids"]) <= set(i["memory_ids"]) for c in conflicts)
        conflicts = resolved + conflicts  # resolved-by-recency conflicts stay visible so the team can see what was set aside
        used = sorted({mid for i in issues for mid in i["memory_ids"]} | {mid for c in conflicts if not c.get("resolution") for mid in c["memory_ids"]})
        review_id = "rev_" + uuid.uuid4().hex[:10]
        mems_out = []
        for m in recalled:
            d = self._with_usage(m)
            d["used"] = m["id"] in used
            d["overridden"] = m["id"] in set_aside
            mems_out.append(d)
        active_out = [m for m in mems_out if not m["overridden"]]
        summary = result["summary"] or reviewer.summarize(issues, memories)
        response = {
            "review_id": review_id,
            "review": summary,
            "issues": issues,
            "memories": mems_out,
            "memory_count": len(recalled),
            "memories_used": used,
            "conflicts": conflicts,
            "code_hash": chash,
            "verdict": insights.verdict(issues),                       # feature 1: instant merge-readiness
            "time_saved_min": insights.minutes_saved(len(issues)),     # feature 1: estimated senior time saved
            "scorecard": insights.scorecard(active_out, issues) if memory_enabled else None,  # feature 4: same checklist every time
            "consistency": insights.consistency(prev["issues"] if prev else None, issues, prev["review_id"] if prev else None),  # feature 4
            "conventions_checked": [m["id"] for m in active_out if not m["used"]] if not issues else [],
            "clean": not issues,
            "groq_latency_ms": groq_ms,
            "latency_ms": int((time.perf_counter() - t_start) * 1000),
            "memory_enabled": memory_enabled,
            "memory_provider": provider_name,
            "review_provider": provider,
            "model": model,
            "review_mode": req.review_mode,
            "pr_title": req.pr_title or "Untitled PR",
            "created_at": now_iso(),
            "warnings": warnings,
        }
        stored = dict(response, code_diff=req.code_diff)
        self.store.add_review(stored)
        if used:
            self.store.record_usage(used, review_id)
            for m in response["memories"]:
                if m["id"] in used:
                    m["times_applied"] += 1
        self.store.add_event("review", review_id=review_id, memory_enabled=memory_enabled, recalled=len(recalled), used=len(used), issues=len(issues))
        return response

    @staticmethod
    def _recall_query(req: ReviewRequest, topics: set[str], parsed: reviewer.Parsed) -> str:
        phrases = ", ".join(TOPIC_PHRASES[t] for t in sorted(topics)) or "general code review conventions"
        return f"Team engineering conventions relevant to reviewing this pull request. PR: {req.pr_title or 'untitled'}. Relevant topics: {phrases}. Code excerpt: {parsed.code[:500]}"

    async def _run_reviewer(self, req, parsed, memories, memory_enabled, warnings):
        if self.settings.groq_configured:
            messages, idmap = llm.build_messages(req.pr_title, req.code_diff, req.review_mode, memories if memory_enabled else None)
            try:
                raw, ms = await llm.call_groq(self.settings, messages)
                self.groq_error = None
                res = llm.normalize(raw, req.code_diff, idmap, memories if memory_enabled else [])
                if not memory_enabled:
                    for i in res["issues"]:
                        i["memory_ids"], i["source_type"] = [], "general"
                    res["conflicts"] = []
                return res, "groq", ms, self.settings.groq_model
            except Exception as e:  # noqa: BLE001 - any provider failure falls back, message is sanitised
                self.groq_error = f"{type(e).__name__}"
                log.warning("Groq review failed (%s); using local engine", type(e).__name__)
                warnings.append(f"Groq unavailable ({type(e).__name__}); used the deterministic local review engine.")
        res = reviewer.local_review(parsed, memories, req.review_mode, memory_enabled)
        return res, "local-deterministic", None, None

    # ---------------------------------------------------------------- teach / seed
    async def teach(self, req: TeachRequest) -> dict[str, Any]:
        prov, mems, warn = await self._retain([{"rule": req.rule, "category": req.category, "source": req.source,
                                                 "owner": req.owner.strip(), "reason": req.reason.strip()}])
        mem = mems[0]
        dup = bool(mem.get("duplicate"))
        if not dup:
            self.store.add_event("teach", category=req.category, provider=prov.name, source=req.source)
        return {
            "success": True, "duplicate": dup, "provider": prov.name, "provider_label": prov.label,
            "memory": self._with_usage(mem), "warning": warn,
            "related": [] if dup else await self._related_rules(mem),
            "message": "Memory already known." if dup else "Memory retained. It will influence future reviews.",
        }

    async def seed(self) -> dict[str, Any]:
        prov, mems, warn = await self._retain([dict(r, source="Seed: team conventions", owner="Seed data") for r in SEED_RULES])
        self.store.add_event("seed", provider=prov.name, count=len(mems))
        return {"success": True, "seeded": len(mems), "provider": prov.name, "provider_label": prov.label, "warning": warn}

    async def memories(self, q: str = "", category: str = "", status: str = "active") -> dict[str, Any]:
        """The rule list. status: 'active' (default, what the reviewer uses), 'inactive' (retired / replaced), 'all'."""
        prov, why = await self.mm.active()
        warn = None
        try:
            raw = await prov.list()
        except MemoryProviderError as e:
            self.mm.mark_failed(str(e))
            prov, raw, warn = self.mm.local, await self.mm.local.list(), f"Hindsight list failed ({e}); showing demo memory."
        every = self._apply_ledger(raw, include_inactive=True)
        active = [m for m in every if m["status"] == lifecycle.ACTIVE]
        counts = {c: 0 for c in CATEGORIES}
        for m in active:
            counts[m["category"]] = counts.get(m["category"], 0) + 1
        pool = every if status == "all" else [m for m in every if (m["status"] == lifecycle.ACTIVE) == (status != "inactive")]
        ql = q.strip().lower()
        shown = [m for m in pool if (not category or category == "All" or m["category"] == category) and (not ql or ql in m["text"].lower())]
        text_of = {m["id"]: m["text"] for m in every}
        items = [self._with_usage(m) for m in shown]
        for it in items:  # let the UI say what replaced a rule (and what a rule replaced) without another round-trip
            it["superseded_by_text"] = text_of.get(it.get("superseded_by"))
            it["supersedes_text"] = text_of.get(it.get("supersedes"))
        return {
            "provider": prov.name, "provider_label": prov.label, "total": len(active), "inactive_total": len(every) - len(active),
            "counts": counts, "status": status,
            "memories": items, "warning": warn or (f"Hindsight unavailable ({why})." if why and self.mm.hindsight else None),
        }

    # ---------------------------------------------------------------- rule lifecycle: edit / retire / restore / replace / delete
    async def edit_memory(self, mem_id: str, req: EditRuleRequest) -> dict[str, Any]:
        if req.rule is None and req.category is None and not (req.reason or "").strip():
            raise ValueError("Nothing to change.")
        _, m, raw = await self._find(mem_id)
        who = self._who(req.actor)

        def fn(s: dict[str, Any]) -> bool:
            rules = s["rule_state"]
            cur = lifecycle.overlay(m, lifecycle.find_state(rules, m))
            new_text = req.rule if req.rule and normalize(req.rule) != normalize(cur["text"]) else None
            if new_text:
                for o in raw:
                    if o["id"] != m["id"] and not lifecycle.is_deleted(s["deleted_rules"], o):
                        ov = lifecycle.overlay(o, lifecycle.find_state(rules, o))
                        if ov["status"] == lifecycle.ACTIVE and normalize(ov["text"]) == normalize(new_text):
                            raise ValueError("Another active rule already says this.")
            st = lifecycle.get_or_create(rules, m)
            changed: list[str] = []
            if new_text:
                st["text"] = new_text
                st["keys"].append(normalize(new_text))
                st["effective_at"] = now_iso()  # reworded rule counts as new guidance for "newest wins"
                changed.append(f"wording: “{lifecycle._short(cur['text'], 60)}” → “{lifecycle._short(new_text, 60)}”")
            if req.category and req.category != cur["category"]:
                st["category"] = req.category
                changed.append(f"category: {cur['category']} → {req.category}")
            if req.reason and req.reason.strip() != cur["reason"]:
                st["reason"] = req.reason.strip()
                changed.append("reason updated")
            if not changed:
                return False
            st["updated_at"], st["updated_by"] = now_iso(), who
            lifecycle.log(st, who, "edited", "; ".join(changed))
            return True

        changed = self.store.write(fn)
        if changed:
            self.store.add_event("rule_edit", memory_id=mem_id)
        return {"success": True, "changed": changed, "memory": self._view(m),
                "message": "Rule updated. New reviews use the new wording." if changed else "No changes to save."}

    async def retire_memory(self, mem_id: str, req: RetireRequest) -> dict[str, Any]:
        _, m, _ = await self._find(mem_id)
        who = self._who(req.actor)

        def fn(s: dict[str, Any]) -> bool:
            st = lifecycle.get_or_create(s["rule_state"], m)
            if st["status"] != lifecycle.ACTIVE:
                return False
            st.update(status=lifecycle.RETIRED, retired_at=now_iso(), retired_by=who, retire_reason=req.reason.strip())
            lifecycle.log(st, who, "retired", req.reason.strip())
            return True

        changed = self.store.write(fn)
        if changed:
            self.store.add_event("rule_retire", memory_id=mem_id)
        return {"success": True, "changed": changed, "memory": self._view(m),
                "message": "Rule retired. Reviews will no longer use it; you can restore it any time." if changed else "This rule is already inactive."}

    async def restore_memory(self, mem_id: str, req: RestoreRequest) -> dict[str, Any]:
        _, m, _ = await self._find(mem_id)
        who = self._who(req.actor)

        def fn(s: dict[str, Any]) -> bool:
            rules = s["rule_state"]
            st = lifecycle.get_or_create(rules, m)
            if st["status"] == lifecycle.ACTIVE:
                return False
            successor = st.get("superseded_by")
            st.update(status=lifecycle.ACTIVE, retired_at=None, retired_by=None, retire_reason=None, superseded_by=None)
            if successor:  # the replacement no longer replaces anything
                for o in rules.values():
                    if o.get("supersedes") and (o["supersedes"] in st["ids"]):
                        o["supersedes"] = None
            lifecycle.log(st, who, "restored", "")
            return True

        changed = self.store.write(fn)
        if changed:
            self.store.add_event("rule_restore", memory_id=mem_id)
        return {"success": True, "changed": changed, "memory": self._view(m),
                "message": "Rule restored. If it disagrees with a newer rule, the newer one wins." if changed else "This rule is already active."}

    async def supersede_memory(self, old_id: str, req: SupersedeRequest) -> dict[str, Any]:
        """Replace an old rule with a newer one (new text, or an existing rule). The old rule stays in history."""
        _, old, raw = await self._find(old_id)
        who = self._who(req.actor)
        old_view = self._apply_ledger([old], include_inactive=True)[0]
        if old_view["status"] != lifecycle.ACTIVE:
            raise ValueError("Only an active rule can be replaced.")
        warn = None
        if req.new_rule_id:
            if req.new_rule_id == old_id:
                raise ValueError("A rule cannot replace itself.")
            _, new, _ = await self._find(req.new_rule_id)
            if self._apply_ledger([new], include_inactive=True)[0]["status"] != lifecycle.ACTIVE:
                raise ValueError("The replacement must be an active rule.")
        elif req.rule:
            if normalize(req.rule) == normalize(old_view["text"]):
                raise ValueError("The new rule is identical to the old one. Edit the rule instead.")
            _, mems, warn = await self._retain([{"rule": req.rule, "category": req.category or old_view["category"], "source": "Replaces an older rule",
                                                 "owner": who if who != "Unknown" else "", "reason": (req.reason or "").strip()}])
            new = mems[0]
            if new["id"] == old["id"]:
                raise ValueError("The new rule is identical to the old one. Edit the rule instead.")
            if not new.get("duplicate"):
                self.store.add_event("teach", category=new["category"], provider="supersede", source="supersede")
        else:
            raise ValueError("Write the replacement rule, or pick an existing rule to replace it with.")

        def fn(s: dict[str, Any]) -> None:
            rules = s["rule_state"]
            old_st, new_st = lifecycle.get_or_create(rules, old), lifecycle.get_or_create(rules, new)
            if old_st is new_st:
                raise ValueError("The new rule is identical to the old one. Edit the rule instead.")
            now = now_iso()
            new_text = new_st.get("text") or new["text"]
            old_st.update(status=lifecycle.SUPERSEDED, superseded_by=new["id"], retired_at=now, retired_by=who,
                          retire_reason=(req.reason or "").strip() or "Replaced by a newer rule")
            new_st.update(supersedes=old["id"], effective_at=now)
            lifecycle.log(old_st, who, "superseded", f"replaced by: “{lifecycle._short(new_text, 80)}”")
            lifecycle.log(new_st, who, "replaces", f"older rule: “{lifecycle._short(old_view['text'], 80)}”")

        self.store.write(fn)
        self.store.add_event("rule_supersede", old_id=old_id, new_id=new["id"])
        return {"success": True, "memory": self._view(new), "superseded": self._view(old), "warning": warn,
                "message": "Rule replaced. The old one is kept in history and reviews now use the new one."}

    async def delete_memory(self, mem_id: str, actor: str = "") -> dict[str, Any]:
        """Permanently remove a rule. Prefer retire/replace when you may want the history back."""
        prov, m, _ = await self._find(mem_id)
        who = self._who(actor)
        view = self._apply_ledger([m], include_inactive=True)[0]
        warn = None
        try:
            removed = await prov.delete(m)
            if prov.name == "hindsight" and not removed:
                warn = "Removed from RepoMind, but Hindsight's own copy could not be located to delete."
        except MemoryProviderError as e:
            warn = f"Removed from RepoMind, but Hindsight could not delete its copy ({e})."

        def fn(s: dict[str, Any]) -> None:
            ids = {m["id"], m.get("document_id")} - {None}
            for key, st in list(s["rule_state"].items()):
                if ids & set(st["ids"]):
                    ids |= set(st["ids"])
                    del s["rule_state"][key]
            for st in s["rule_state"].values():  # unlink anything that pointed at the deleted rule
                if st.get("supersedes") in ids:
                    st["supersedes"] = None
            if prov.name == "hindsight":  # local memories are really removed; Hindsight units may lag, so hide them too
                s["deleted_rules"].append({"ids": sorted(ids), "at": now_iso(), "by": who, "text": lifecycle._short(view["text"], 120)})

        self.store.write(fn)
        self.store.add_event("rule_delete", memory_id=mem_id)
        return {"success": True, "deleted": mem_id, "warning": warn, "message": "Rule deleted permanently."}

    async def _related_rules(self, mem: dict[str, Any]) -> list[dict[str, Any]]:
        """Older active rules on the same topic as a freshly taught rule: candidates for 'this replaces that'."""
        try:
            data = await self.memories()
        except MemoryProviderError:
            return []
        topics, key = set(mem.get("topics") or memory_topics(mem["text"])), normalize(mem["text"])
        scored = []
        for o in data["memories"]:
            if o["id"] == mem["id"] or normalize(o["text"]) == key:
                continue
            overlap = len(topics & set(memory_topics(o["text"])))
            if overlap:
                scored.append((overlap, o))
        scored.sort(key=lambda t: -t[0])
        return [{"id": o["id"], "text": o["text"], "category": o["category"], "given_by": o.get("given_by", ""), "given_at": o.get("given_at")}
                for _, o in scored[:3]]

    # ---------------------------------------------------------------- feedback
    async def feedback(self, req: FeedbackRequest) -> dict[str, Any]:
        review = self.store.get_review(req.review_id)
        if not review:
            raise KeyError("review not found")
        issue = None
        if req.issue_id:
            issue = next((i for i in review["issues"] if i["id"] == req.issue_id), None)
            if not issue:
                raise KeyError("issue not found")
        self.store.add_feedback({"review_id": req.review_id, "issue_id": req.issue_id, "type": req.feedback_type, "comment": req.comment, "taught": req.teach_as_rule, "at": now_iso()})
        self.store.add_event("feedback", review_id=req.review_id, issue_id=req.issue_id, feedback_type=req.feedback_type, taught=req.teach_as_rule)
        memory = None
        provider = None
        warn = None
        if req.teach_as_rule:
            if req.comment.strip():
                rule = " ".join(req.comment.split())
            elif issue:
                rule = f"Team convention: {issue['recommendation']} (flagged as: {issue['title']})"
            else:
                raise ValueError("Add a note describing the rule to teach.")
            if len(rule) < 8:
                raise ValueError("The rule is too short to teach.")
            cat = req.category or (issue["category"] if issue else "Other")
            prov, mems, warn = await self._retain([{"rule": rule[:1000], "category": cat, "source": "Developer feedback"}])
            provider, memory = prov.name, self._with_usage(mems[0])
            if not mems[0].get("duplicate"):
                self.store.add_event("teach", category=cat, provider=prov.name, source="feedback")
        return {"success": True, "stored": True, "taught": bool(memory), "provider": provider, "memory": memory, "warning": warn,
                "message": "Memory retained from your feedback." if memory else "Feedback recorded."}

    # ---------------------------------------------------------------- history / analytics / DNA
    def history(self) -> list[dict[str, Any]]:
        rows = self.store.read(lambda s: list(s["reviews"]))
        return [{"review_id": r["review_id"], "pr_title": r["pr_title"], "created_at": r["created_at"], "review_mode": r["review_mode"],
                 "issues_found": len(r["issues"]), "memories_used": len(r["memories_used"]), "memories_recalled": r["memory_count"],
                 "memory_enabled": r["memory_enabled"], "memory_provider": r["memory_provider"],
                 "verdict": (r.get("verdict") or {}).get("status")} for r in reversed(rows)]

    def review_detail(self, review_id: str) -> dict[str, Any] | None:
        r = self.store.get_review(review_id)
        if not r:
            return None
        fb = self.store.read(lambda s: [f for f in s["feedback"] if f["review_id"] == review_id])
        return dict(r, feedback={f["issue_id"] or "_review": f["type"] for f in fb})

    def analytics(self) -> dict[str, Any]:
        def fn(s: dict[str, Any]) -> dict[str, Any]:
            ev = s["events"]
            reviews = [e for e in ev if e["type"] == "review"]
            fbs = [e for e in ev if e["type"] == "feedback"]
            return {
                "total_reviews": len(reviews),
                "memory_backed_reviews": sum(1 for e in reviews if e["memory_enabled"] and e["used"] > 0),
                "memory_enabled_reviews": sum(1 for e in reviews if e["memory_enabled"]),
                "rules_taught": sum(1 for e in ev if e["type"] == "teach"),
                "memories_recalled": sum(e["recalled"] for e in reviews if e["memory_enabled"]),
                "accepted_suggestions": sum(1 for e in fbs if e["feedback_type"] == "accepted"),
                "rejected_suggestions": sum(1 for e in fbs if e["feedback_type"] == "rejected"),
                "marked_helpful": sum(1 for e in fbs if e["feedback_type"] == "helpful"),
                "marked_incorrect": sum(1 for e in fbs if e["feedback_type"] == "incorrect"),
            }
        return self.store.read(fn)

    async def dna(self) -> dict[str, Any]:
        data = await self.memories()
        mems = data["memories"]
        sections = {}
        for cat in ["Architecture", "Database", "API", "Security", "Logging", "Testing", "Performance"]:
            rules = [m for m in mems if m["category"] == cat]
            sections[cat] = {"count": len(rules), "rules": [{"id": m["id"], "text": m["text"]} for m in rules[:3]]}
        tech = sorted({t for m in mems for t in detect_tech(m["text"])})
        return {"provider": data["provider"], "provider_label": data["provider_label"], "team_rules": len(mems), "sections": sections,
                "other_rules": sum(1 for m in mems if m["category"] == "Other"), "technologies": tech}

    # ---------------------------------------------------------------- team impact (the five pain points)
    async def compare(self, req: ReviewRequest) -> dict[str, Any]:
        """Feature 5: run a generic review and a team-aware review side by side and show what memory added."""
        common = dict(code_diff=req.code_diff, pr_title=req.pr_title, review_mode=req.review_mode)
        plain, mem = await asyncio.gather(self.review(ReviewRequest(bypass_memory=True, **common)),
                                          self.review(ReviewRequest(bypass_memory=False, **common)))
        return {"plain": plain, "memory": mem, "delta": insights.compare_delta(plain["issues"], mem["issues"])}

    async def impact(self) -> dict[str, Any]:
        data = insights.build_impact(self.store.reviews_snapshot())
        mems = (await self.memories())["memories"]
        data["knowledge"] = insights.knowledge_stats(mems)
        return data

    async def playbook(self) -> dict[str, Any]:
        mems = (await self.memories())["memories"]
        return {"filename": "team-playbook.md", "count": len(mems), "markdown": insights.playbook_markdown(mems, now_iso())}
