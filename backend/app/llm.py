"""Groq integration: memory-aware and stateless review prompts, JSON parsing, and normalisation.

Anything the model returns is validated: unknown memory ids are dropped, so the model can never
cite a memory that was not actually recalled.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from .config import Settings

SYSTEM_MEMORY = """You are reviewing code for a specific engineering team.

The supplied memories are persistent team knowledge.

Use them when relevant.

Do not invent memories.

When a recommendation is based on a memory,
cite the relevant memory.

Distinguish general best practices from
repository-specific conventions.

If memories conflict, explain the conflict
instead of silently choosing one."""

SYSTEM_STATELESS = (
    "You are a generic AI code reviewer. You have no knowledge of this team's conventions, repository, "
    "architecture or history. Review using only general best practices and never claim to know team rules."
)

MODE_FOCUS = {
    "general": "General review: correctness, error handling, readability, logging and obvious security problems.",
    "security": "Security review: SQL injection, unsafe SQL interpolation, secret exposure, sensitive logging, authentication and authorization gaps, unsafe user input, missing validation.",
    "architecture": "Architecture review: layering, separation of concerns, data-access boundaries, API design and conventions.",
    "performance": "Performance review: N+1 queries, missing timeouts, unbounded work, wasteful queries.",
    "full": "Full review: cover correctness, security, architecture and performance.",
}

JSON_INSTRUCTIONS = """Respond with ONE JSON object and nothing else, in this shape:
{
  "summary": "one or two sentences",
  "issues": [
    {
      "severity": "critical|high|medium|low",
      "title": "short title",
      "description": "what is wrong",
      "line": <line number from the numbered code, or null>,
      "recommendation": "what to do",
      "reason": "why this is a problem",
      "memory_ids": ["M1"],
      "source_type": "team|general"
    }
  ],
  "conflicts": [ {"memory_ids": ["M1","M2"], "explanation": "why they conflict"} ]
}
Rules: report only real problems visible in the code. If the code is fine return "issues": [].
"memory_ids" must list only ids from the supplied memories that justify the issue (empty for general best practices)."""


def number_lines(text: str) -> str:
    return "\n".join(f"{i:>3}| {ln}" for i, ln in enumerate(text.splitlines(), 1))


def build_messages(pr_title: str, code_diff: str, mode: str, memories: list[dict[str, Any]] | None) -> tuple[list[dict[str, str]], dict[str, str]]:
    idmap: dict[str, str] = {}
    parts = [f"PR title: {pr_title or '(untitled)'}", f"Review mode — {MODE_FOCUS[mode]}"]
    if memories:
        lines = []
        for n, m in enumerate(memories, 1):
            idmap[f"M{n}"] = m["id"]
            lines.append(f"[M{n}] ({m['category']}) {m['text']}")
        parts.append("Team memories recalled from Hindsight:\n" + "\n".join(lines))
    elif memories is not None:
        parts.append("Team memories recalled from Hindsight: none relevant.")
    parts.append("Code under review (line-numbered):\n" + number_lines(code_diff))
    system = (SYSTEM_MEMORY if memories is not None else SYSTEM_STATELESS) + "\n\n" + JSON_INSTRUCTIONS
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(parts)}], idmap


def parse_json(text: str) -> dict[str, Any]:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("model did not return JSON")
    return json.loads(text[start : end + 1])


async def call_groq(settings: Settings, messages: list[dict[str, str]]) -> tuple[dict[str, Any], int]:
    from groq import AsyncGroq

    client = AsyncGroq(api_key=settings.groq_api_key, timeout=30.0, max_retries=1)
    t0 = time.perf_counter()
    extra: dict[str, Any] = {}
    if settings.groq_model.startswith("openai/gpt-oss"):
        extra["reasoning_effort"] = "low"  # keep reasoning tokens from eating the JSON output budget
    resp = await client.chat.completions.create(
        model=settings.groq_model,
        messages=messages,
        temperature=0.1,
        max_tokens=4000,
        response_format={"type": "json_object"},
        **extra,
    )
    latency = int((time.perf_counter() - t0) * 1000)
    return parse_json(resp.choices[0].message.content or ""), latency


SEVERITIES = {"critical", "high", "medium", "low", "info"}
SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def normalize(raw: dict[str, Any], code_diff: str, idmap: dict[str, str], memories: list[dict[str, Any]]) -> dict[str, Any]:
    lines = code_diff.splitlines()
    by_id = {m["id"]: m for m in memories}
    issues = []
    for it in (raw.get("issues") or [])[:10]:
        if not isinstance(it, dict) or not it.get("title"):
            continue
        sev = str(it.get("severity", "medium")).lower()
        sev = sev if sev in SEVERITIES else "medium"
        mids = []
        for ref in it.get("memory_ids") or []:
            real = idmap.get(str(ref)) or (str(ref) if str(ref) in by_id else None)
            if real and real not in mids:
                mids.append(real)
        line = it.get("line")
        line = line if isinstance(line, int) and 1 <= line <= len(lines) else None
        cat = by_id[mids[0]]["category"] if mids else "Other"
        issues.append({
            "severity": sev, "title": str(it["title"])[:160], "description": str(it.get("description", ""))[:600],
            "line": line, "code": lines[line - 1].lstrip("+ ").strip() if line else "",
            "recommendation": str(it.get("recommendation", ""))[:500], "reason": str(it.get("reason", ""))[:600],
            "category": cat, "memory_ids": mids, "source_type": "team" if mids else "general",
        })
    issues.sort(key=lambda i: (SEV_ORDER[i["severity"]], i["line"] or 0))
    for n, i in enumerate(issues, 1):
        i["id"] = f"i{n}"
    conflicts = []
    for c in raw.get("conflicts") or []:
        mids = [idmap.get(str(r)) for r in (c.get("memory_ids") or [])]
        mids = [m for m in mids if m]
        if len(mids) >= 2:
            conflicts.append({"memory_ids": mids[:2], "topic": "", "explanation": str(c.get("explanation", ""))[:600]})
    return {"issues": issues, "summary": str(raw.get("summary", ""))[:400], "conflicts": conflicts}
