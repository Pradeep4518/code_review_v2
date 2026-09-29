"""Memory providers.

HindsightMemoryProvider  - real Hindsight Cloud REST API (retain / recall / list).
LocalMemoryProvider      - explicit fallback used ONLY when Hindsight is unavailable.
                           It is always labelled "DEMO MEMORY MODE" and never as Hindsight.

Hindsight endpoints used (see https://hindsight.vectorize.io/api-reference):
  PUT  /v1/default/banks/{bank_id}                  create/update bank (missions)
  POST /v1/default/banks/{bank_id}/memories         retain   {items:[{content,context,document_id,tags,metadata}]}
  POST /v1/default/banks/{bank_id}/memories/recall  recall   {query,budget,max_tokens,types}
  GET  /v1/default/banks/{bank_id}/memories/list    list     ?limit&offset&q
Auth: Authorization: Bearer <HINDSIGHT_API_KEY>
"""
from __future__ import annotations

import re
import time
import uuid
from typing import Any

import httpx

from .config import Settings
from .store import Store, now_iso
from .topics import classify_category, memory_topics


class MemoryProviderError(Exception):
    """Raised for any provider failure. Messages never contain credentials."""


def normalize(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def finish(mem: dict[str, Any]) -> dict[str, Any]:
    mem["topics"] = memory_topics(mem["text"])
    mem["label"] = f"{mem['category']} rule"
    return mem


# --------------------------------------------------------------------------
# Local fallback
# --------------------------------------------------------------------------
class LocalMemoryProvider:
    name = "local"
    label = "DEMO MEMORY MODE"

    def __init__(self, store: Store):
        self.store = store

    async def retain(self, rules: list[dict[str, str]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []

        def fn(s: dict[str, Any]) -> None:
            existing = {normalize(m["text"]): m for m in s["local_memories"]}
            for r in rules:
                key = normalize(r["rule"])
                if key in existing:
                    for f in ("owner", "reason"):  # fill in missing knowledge on a repeat teach
                        if r.get(f) and not existing[key].get(f):
                            existing[key][f] = r[f]
                    m = dict(existing[key], duplicate=True)
                else:
                    m = {
                        "id": "mem_" + uuid.uuid4().hex[:8],
                        "text": r["rule"],
                        "category": r["category"],
                        "source": r.get("source", "developer"),
                        "owner": r.get("owner", ""),
                        "reason": r.get("reason", ""),
                        "created_at": now_iso(),
                    }
                    s["local_memories"].append(m)
                    existing[key] = m
                out.append(m)

        self.store.write(fn)
        return [finish(dict(m)) for m in out]

    async def recall(self, query: str, topics: set[str], limit: int = 8) -> list[dict[str, Any]]:
        mems = [finish(dict(m)) for m in self.store.read(lambda s: list(s["local_memories"]))]
        q_tokens = set(normalize(query).split())
        scored = []
        for m in mems:
            overlap = len(set(m["topics"]) & topics)
            if not overlap:
                continue
            text_overlap = len(q_tokens & set(normalize(m["text"]).split()))
            scored.append((overlap * 10 + text_overlap, m))
        scored.sort(key=lambda t: -t[0])
        return [m for _, m in scored[:limit]]

    async def list(self) -> list[dict[str, Any]]:
        mems = self.store.read(lambda s: list(s["local_memories"]))
        return [finish(dict(m)) for m in sorted(mems, key=lambda m: m["created_at"], reverse=True)]

    async def delete(self, mem: dict[str, Any]) -> bool:
        """Permanently remove one memory from the local demo store."""
        def fn(s: dict[str, Any]) -> bool:
            before = len(s["local_memories"])
            s["local_memories"] = [m for m in s["local_memories"] if m["id"] != mem["id"]]
            return len(s["local_memories"]) < before

        return self.store.write(fn)

    async def ping(self) -> dict[str, Any]:
        return {"ok": True, "total": len(self.store.read(lambda s: s["local_memories"]))}


# --------------------------------------------------------------------------
# Hindsight Cloud
# --------------------------------------------------------------------------
BANK_RETAIN_MISSION = (
    "Extract engineering team conventions, architectural rules, security policies and coding "
    "standards as standalone rule statements. Keep the original rule wording and keep the category."
)
BANK_REFLECT_MISSION = "I am the institutional memory of an engineering team's code review conventions."


class HindsightMemoryProvider:
    name = "hindsight"
    label = "HINDSIGHT CONNECTED"

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.s = settings
        self.bank = settings.hindsight_bank_id
        self._transport = transport
        self._list_cache: tuple[float, list[dict[str, Any]], int] | None = None

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.s.hindsight_base_url,
            headers={"Authorization": f"Bearer {self.s.hindsight_api_key}", "Content-Type": "application/json"},
            timeout=timeout,
            transport=self._transport,
        )

    async def _request(self, method: str, path: str, *, timeout: float = 20.0, **kw: Any) -> httpx.Response:
        try:
            async with self._client(timeout) as c:
                resp = await c.request(method, path, **kw)
        except httpx.TimeoutException as e:
            raise MemoryProviderError("Hindsight request timed out") from e
        except httpx.HTTPError as e:
            raise MemoryProviderError(f"Hindsight unreachable ({type(e).__name__})") from e
        return resp

    @staticmethod
    def _check(resp: httpx.Response, what: str) -> None:
        if resp.status_code in (401, 403):
            raise MemoryProviderError(f"Hindsight rejected the API key while trying to {what} (HTTP {resp.status_code})")
        if resp.status_code >= 400:
            raise MemoryProviderError(f"Hindsight returned HTTP {resp.status_code} while trying to {what}")

    async def ensure_bank(self) -> None:
        resp = await self._request(
            "PUT",
            f"/v1/default/banks/{self.bank}",
            json={"retain_mission": BANK_RETAIN_MISSION, "reflect_mission": BANK_REFLECT_MISSION},
        )
        self._check(resp, "create the memory bank")

    async def ping(self) -> dict[str, Any]:
        resp = await self._request("GET", f"/v1/default/banks/{self.bank}/memories/list", params={"limit": 1})
        if resp.status_code == 404:
            await self.ensure_bank()
            return {"ok": True, "total": 0}
        self._check(resp, "reach the memory bank")
        return {"ok": True, "total": resp.json().get("total", 0)}

    async def retain(self, rules: list[dict[str, str]]) -> list[dict[str, Any]]:
        items = []
        for r in rules:
            doc_id = r.get("key") or "rule-" + uuid.uuid4().hex[:10]
            items.append(
                {
                    "content": f"Team engineering rule ({r['category']}): {r['rule']}",
                    "context": f"RepoMind team convention. Category: {r['category']}. Source: {r.get('source', 'developer')}."
                    + (f" Why the rule exists: {r['reason']}." if r.get("reason") else ""),
                    "document_id": doc_id,
                    "tags": ["repomind", f"category:{r['category'].lower()}"],
                    "metadata": {"category": r["category"], "source": r.get("source", "developer"),
                                 **({"owner": r["owner"]} if r.get("owner") else {}), **({"reason": r["reason"]} if r.get("reason") else {})},
                }
            )
        resp = await self._request(
            "POST", f"/v1/default/banks/{self.bank}/memories", json={"items": items, "async": False}, timeout=90.0
        )
        if resp.status_code == 404:
            await self.ensure_bank()
            resp = await self._request(
                "POST", f"/v1/default/banks/{self.bank}/memories", json={"items": items, "async": False}, timeout=90.0
            )
        self._check(resp, "retain memory")
        self._list_cache = None
        return [
            finish(
                {
                    "id": it["document_id"],
                    "document_id": it["document_id"],
                    "text": r["rule"],
                    "category": r["category"],
                    "source": r.get("source", "developer"),
                    "owner": r.get("owner", ""),
                    "reason": r.get("reason", ""),
                    "created_at": now_iso(),
                    "pending_extraction": True,
                }
            )
            for it, r in zip(items, rules)
        ]

    def _from_unit(self, u: dict[str, Any]) -> dict[str, Any]:
        tags = u.get("tags") or []
        meta = u.get("metadata") or {}
        cat = meta.get("category")
        if not cat:
            cat = next((t.split(":", 1)[1].capitalize() for t in tags if t.startswith("category:")), None)
        text = u["text"]
        text = re.sub(r"^Team engineering rule \([^)]*\):\s*", "", text)
        return finish(
            {
                "id": u["id"],
                "text": text,
                "category": cat if cat in {"Architecture", "Database", "Security", "Logging", "Testing", "API", "Performance", "Other"} else classify_category(text),
                "source": meta.get("source") or u.get("context") or "Hindsight",
                "owner": meta.get("owner") or "",
                "reason": meta.get("reason") or "",
                "created_at": u.get("date") or u.get("mentioned_at") or u.get("occurred_start"),
                "fact_type": u.get("fact_type") or u.get("type"),
                "document_id": u.get("document_id"),
            }
        )

    async def list(self) -> list[dict[str, Any]]:
        if self._list_cache and time.time() - self._list_cache[0] < 10:
            return self._list_cache[1]
        resp = await self._request("GET", f"/v1/default/banks/{self.bank}/memories/list", params={"limit": 200})
        if resp.status_code == 404:
            return []
        self._check(resp, "list memories")
        data = resp.json()
        mems = [self._from_unit(u) for u in data.get("items", [])]
        self._list_cache = (time.time(), mems, data.get("total", len(mems)))
        return mems

    async def delete(self, mem: dict[str, Any]) -> bool:
        """Delete the Hindsight document behind a rule (cascades to its extracted memory units).

        DELETE /v1/default/banks/{bank_id}/documents/{document_id}. Returns False when the document id
        is unknown; raises MemoryProviderError on an API failure so the caller can report it honestly.
        """
        doc_id = mem.get("document_id")
        if not doc_id:
            return False
        resp = await self._request("DELETE", f"/v1/default/banks/{self.bank}/documents/{doc_id}")
        if resp.status_code == 404:
            return True  # already gone
        self._check(resp, "delete the memory")
        self._list_cache = None
        return True

    async def total(self) -> int:
        await self.list()
        return self._list_cache[2] if self._list_cache else 0

    async def recall(self, query: str, topics: set[str], limit: int = 8) -> list[dict[str, Any]]:
        resp = await self._request(
            "POST",
            f"/v1/default/banks/{self.bank}/memories/recall",
            json={"query": query, "budget": "mid", "max_tokens": 1500, "types": ["world", "experience"]},
            timeout=30.0,
        )
        if resp.status_code == 404:
            return []
        self._check(resp, "recall memories")
        by_id: dict[str, dict[str, Any]] = {}
        try:
            by_id = {m["id"]: m for m in await self.list()}
        except MemoryProviderError:
            pass
        out = []
        for r in resp.json().get("results", []):
            base = by_id.get(r["id"]) or self._from_unit(r)
            out.append(dict(base))
        out.sort(key=lambda m: 0 if set(m["topics"]) & topics else 1)  # stable: keep Hindsight rank within groups
        return out[:limit]
