"""DeterministicLocalReviewProvider + shared review helpers.

The local engine is a rule-based fallback (no LLM). Findings are produced from the code alone;
team memories only ever ATTACH to a finding (or unlock team-only findings) when a recalled memory
really covers the same topic. It never invents memories.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from .topics import MODE_TOPICS

SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

SQL_RE = re.compile(r"\b(select\s.+\sfrom|insert\s+into|update\s+\w+\s+set|delete\s+from)\b", re.I)
INTERP_RE = re.compile(r"""\bf["']|\.format\(|["']\s*\+\s*\w|\w\s*\+\s*["']|["']\s*%\s*[\(\w]""")
ROUTE_RE = re.compile(r"@\w+\.(get|post|put|patch|delete)\(|\b(router|app)\.(get|post|put|patch|delete)\(", re.I)
DB_CALL_RE = re.compile(r"\.execute\(|\bcursor\b|session\.(query|execute)|\bdb\.(query|execute)|\bconnection\.", re.I)
LOG_CALL_RE = re.compile(r"\bprint\(|console\.\w+\(|\blogger?\.\w+\(|\blogging\.\w+\(", re.I)
SENSITIVE_RE = re.compile(r"password|passwd|secret|token|authorization|api[_-]?key|bearer", re.I)
SECRET_RE = re.compile(r"""(?i)\b(api[_-]?key|secret|password|passwd|token)\b\s*[:=]\s*["'][^"'\s]{6,}["']""")
DEF_RE = re.compile(r"^\s*(async\s+)?def\s+\w+\(([^)]*)\)\s*(->\s*[^:]+)?:")


@dataclass
class Parsed:
    text: str
    lines: list[tuple[int, str]]
    files: list[str]
    lang: str

    def find(self, pattern: re.Pattern[str], pred: Callable[[str], bool] | None = None) -> tuple[int, str] | None:
        for n, t in self.lines:
            if pattern.search(t) and (pred is None or pred(t)):
                return n, t
        return None

    def find_all(self, pattern: re.Pattern[str]) -> list[tuple[int, str]]:
        return [(n, t) for n, t in self.lines if pattern.search(t)]

    @property
    def code(self) -> str:
        return "\n".join(t for _, t in self.lines)


def parse_diff(text: str) -> Parsed:
    lines: list[tuple[int, str]] = []
    files: list[str] = []
    for i, raw in enumerate(text.splitlines(), 1):
        if raw.startswith("+++"):
            p = raw[3:].strip()
            p = p[2:] if p.startswith("b/") else p
            if p and p != "/dev/null":
                files.append(p)
            continue
        if raw.startswith(("---", "@@", "diff ", "index ")) or raw.startswith("-"):
            continue
        body = raw[1:] if raw.startswith("+") else raw
        lines.append((i, body))
    js_files = any(f.endswith((".js", ".ts", ".jsx", ".tsx")) for f in files)
    py_files = any(f.endswith(".py") for f in files)
    joined = "\n".join(t for _, t in lines)
    if js_files and not py_files:
        lang = "js"
    elif py_files:
        lang = "python"
    else:
        lang = "js" if re.search(r"console\.|=>|\bfunction\b|\bconst\b", joined) and not re.search(r"\bdef\s", joined) else "python"
    return Parsed(text=text, lines=lines, files=files, lang=lang)


def in_route(p: Parsed) -> bool:
    return bool(p.find(ROUTE_RE)) or any(re.search(r"(^|/)(routes?|api|endpoints?|handlers?)/", f) for f in p.files)


def detect_topics(p: Parsed, mode: str) -> set[str]:
    """Which team-knowledge topics are relevant to this code (used to steer recall)."""
    t: set[str] = set()
    if p.find(SQL_RE) or p.find(DB_CALL_RE):
        t |= {"database_access", "sql_safety"}
    if in_route(p):
        t |= {"api_thin", "api_contract", "auth", "validation"}
    if p.find(LOG_CALL_RE):
        t.add("logging")
        if any(SENSITIVE_RE.search(x) for _, x in p.find_all(LOG_CALL_RE)):
            t |= {"sensitive_logging"}
    if re.search(r"\bawait\b|\basync\b|fetch\(|requests\.|httpx\.", p.code):
        t.add("error_handling")
    if p.lang == "python" and p.find(DEF_RE):
        t |= {"typing", "testing"}
    if p.find(SECRET_RE):
        t.add("secrets")
    if re.search(r"\bfor\b.*:|\bfor\s*\(", p.code) and p.find(DB_CALL_RE):
        t.add("performance")
    allowed = MODE_TOPICS.get(mode)
    return t & allowed if allowed else t


def _sev_counts(issues: list[dict[str, Any]]) -> str:
    c: dict[str, int] = {}
    for i in issues:
        c[i["severity"]] = c.get(i["severity"], 0) + 1
    return ", ".join(f"{n} {s}" for s, n in sorted(c.items(), key=lambda kv: SEV_ORDER[kv[0]]))


# --------------------------------------------------------------------------
# Detectors
# --------------------------------------------------------------------------
def F(key: str, modes: tuple[str, ...], severity: str, title: str, description: str, recommendation: str, reason: str,
      hit: tuple[int, str] | None, topics: list[str], category: str, team_only: bool = False,
      mem_filter: Callable[[dict[str, Any]], bool] | None = None, team_title: str | None = None) -> dict[str, Any]:
    return {
        "key": key, "modes": modes, "severity": severity, "title": title, "description": description,
        "recommendation": recommendation, "reason": reason,
        "line": hit[0] if hit else None, "code": hit[1].strip() if hit else "",
        "topics": topics, "category": category, "team_only": team_only, "mem_filter": mem_filter, "team_title": team_title,
    }


def run_detectors(p: Parsed) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    code = p.code
    sql_line = p.find(SQL_RE)
    interp_sql = p.find(SQL_RE, lambda t: bool(INTERP_RE.search(t)))
    exec_line = p.find(re.compile(r"\.execute\(", re.I))
    is_repo_file = any("repository" in f for f in p.files)
    route = in_route(p)
    db_line = exec_line or p.find(DB_CALL_RE) or sql_line

    if interp_sql:
        out.append(F("sql_injection", ("general", "security", "full"), "critical", "SQL Injection Risk",
                     "User-controlled input is interpolated directly into a SQL string.",
                     "Use parameterized queries (bound parameters) instead of building SQL with string interpolation.",
                     "Interpolating values into SQL lets an attacker change the meaning of the query.",
                     interp_sql, ["sql_safety", "database_access"], "Security"))
    elif sql_line and exec_line:
        out.append(F("raw_sql", ("general", "architecture", "security", "full"), "medium", "Raw SQL in application code",
                     "Hand-written SQL is executed directly from application code.",
                     "Prefer an ORM or a dedicated data-access layer so queries are reviewed in one place.",
                     "Scattered raw SQL is hard to audit and easy to get wrong.",
                     sql_line, ["sql_safety"], "Database"))

    if db_line and not is_repo_file:
        out.append(F("repo_violation", ("general", "architecture", "security", "full"), "high",
                     "Team convention violated: database access bypasses the repository layer",
                     "This code talks to the database directly instead of going through the team's data-access layer.",
                     "Move this query behind the repository layer and call it from here.",
                     "A recorded team rule covers how database access must be structured.",
                     db_line, ["database_access"], "Database", team_only=True,
                     mem_filter=lambda m: "database_access" in m["topics"]))

    if route and db_line and not is_repo_file:
        out.append(F("route_not_thin", ("general", "architecture", "full"), "high",
                     "Team convention violated: route handler is not thin",
                     "The API route handler performs database work itself instead of delegating.",
                     "Keep the handler to request parsing and response shaping; delegate data access to a service/repository.",
                     "A recorded team rule says API route handlers must stay thin.",
                     db_line, ["api_thin"], "Architecture", team_only=True))

    if interp_sql or (sql_line and exec_line):
        if exec_line and not re.search(r"timeout", code, re.I) and not is_repo_file:
            out.append(F("sql_timeout", ("security", "performance", "full"), "medium",
                         "Team convention violated: SQL runs without a statement timeout",
                         "No statement timeout is set for this SQL execution.",
                         "Set the team's statement timeout on the connection or query.",
                         "A recorded team rule requires timeouts on SQL execution.",
                         exec_line, ["sql_safety"], "Security", team_only=True,
                         mem_filter=lambda m: "timeout" in m["text"].lower()))

    prints = p.find_all(re.compile(r"\bprint\(|console\.(log|debug|info)\("))
    if prints:
        n, t = prints[0]
        many = f" ({len(prints)} occurrences)" if len(prints) > 1 else ""
        out.append(F("debug_output", ("general", "full"), "low", "Debug output left in code",
                     f"print/console output is used for diagnostics{many}.",
                     "Remove it or replace it with a real logger.",
                     "Ad-hoc console output is unstructured and easy to leave in production.",
                     (n, t), ["logging"], "Logging", team_title="Team convention violated: use structured logging"))

    sens = [(n, t) for n, t in p.find_all(LOG_CALL_RE) if SENSITIVE_RE.search(t)]
    if sens:
        out.append(F("sensitive_logging", ("security", "general", "full"), "high", "Sensitive data written to logs",
                     "A log statement appears to include a token, password, secret or authorization value.",
                     "Remove the sensitive value from the log line or redact it before logging.",
                     "Logs are widely readable and long-lived; secrets in logs leak.",
                     sens[0], ["sensitive_logging"], "Security"))

    sec = p.find(SECRET_RE)
    if sec:
        out.append(F("hardcoded_secret", ("security", "general", "full"), "critical", "Hardcoded credential",
                     "A secret-looking value is assigned as a string literal in source.",
                     "Load the value from an environment variable or secret manager and rotate the exposed one.",
                     "Secrets committed to source control are exposed to everyone with repo access.",
                     sec, ["secrets"], "Security"))

    awaits = p.find(re.compile(r"\bawait\b"))
    if awaits:
        has_try = bool(re.search(r"\btry\s*[:{]", code) or re.search(r"\.catch\(", code))
        if not has_try:
            extra = " The response status is also never checked (no `.ok` test)." if "fetch(" in code and ".ok" not in code else ""
            out.append(F("async_error", ("general", "full"), "medium", "Missing error handling around async call",
                         "An awaited call has no surrounding try/except (or try/catch), so failures propagate unhandled." + extra,
                         "Wrap the awaited call in error handling and surface a meaningful error to the caller.",
                         "Unhandled rejections crash requests or leave the UI in a broken state.",
                         awaits, ["error_handling"], "Architecture"))

    if route:
        deco = p.find(ROUTE_RE)
        if deco and not re.search(r"Depends\(|get_current_user|requireAuth|authenticate", code):
            out.append(F("missing_auth", ("security", "full"), "medium", "Endpoint has no visible authentication",
                         "No authentication dependency is visible for this endpoint in the diff.",
                         "Protect the endpoint with the project's authentication dependency, or document why it is public.",
                         "Unauthenticated endpoints can expose data to anyone who can reach the API.",
                         deco, ["auth"], "Security"))
        raw_in = p.find(re.compile(r"request\.json\(|request\.query_params|:\s*dict\b|Body\(\.\.\.\)|req\.body"))
        if raw_in:
            out.append(F("missing_validation", ("security", "general", "full"), "medium", "Unvalidated user input",
                         "Request data is read without a validated schema.",
                         "Declare a Pydantic model (or equivalent schema) for the request body and query parameters.",
                         "Unvalidated input leads to injection, crashes and inconsistent data.",
                         raw_in, ["validation"], "Security"))

    loop = None
    for idx, (n, t) in enumerate(p.lines):
        if re.match(r"\s*for\b", t):
            indent = len(t) - len(t.lstrip())
            for n2, t2 in p.lines[idx + 1: idx + 8]:
                if t2.strip() and len(t2) - len(t2.lstrip()) > indent and DB_CALL_RE.search(t2):
                    loop = (n2, t2)
                    break
        if loop:
            break
    if loop:
        out.append(F("query_in_loop", ("performance", "full"), "medium", "Query inside a loop (possible N+1)",
                     "A database call is made on every loop iteration.",
                     "Fetch the rows in one query (IN clause / join) and group them in memory.",
                     "Per-row queries multiply latency with data size.", loop, ["performance"], "Performance"))
    star = p.find(re.compile(r"select\s+\*", re.I))
    if star:
        out.append(F("select_star", ("performance", "full"), "low", "SELECT * fetches unneeded columns",
                     "Selecting every column moves more data than needed.", "List only the columns the caller uses.",
                     "Wide selects cost bandwidth and break when the schema changes.", star, ["performance"], "Performance"))
    nt = p.find(re.compile(r"\b(requests|httpx)\.(get|post|put|delete|patch)\("), lambda t: "timeout" not in t)
    if nt:
        out.append(F("no_http_timeout", ("performance", "security", "full"), "medium", "Outbound HTTP call without a timeout",
                     "The request can hang indefinitely.", "Pass an explicit timeout.",
                     "A slow dependency should not be able to stall your service.", nt, ["performance"], "Performance"))

    if p.lang == "python":
        bad = None
        for n, t in p.lines:
            m = DEF_RE.match(t)
            if not m:
                continue
            args = [a.strip() for a in m.group(2).split(",") if a.strip() and a.strip() not in ("self", "cls")]
            if not m.group(3) or any(":" not in a and not a.startswith("*") for a in args):
                bad = (n, t)
                break
        if bad:
            out.append(F("untyped", ("full",), "low", "Team convention violated: missing type annotations",
                         "A function is missing parameter or return type annotations.",
                         "Add full type annotations to the signature.", "A recorded team rule requires typed Python.",
                         bad, ["typing"], "Other", team_only=True))
        has_tests = any("test" in f for f in p.files) or p.find(re.compile(r"def test_"))
        if not has_tests and p.find(DEF_RE):
            out.append(F("missing_tests", ("full",), "low", "Team convention violated: no tests in this change",
                         "This change adds behavior but includes no test.", "Add a pytest test covering the new behavior.",
                         "A recorded team rule requires tests to ship with behavior changes.",
                         None, ["testing"], "Testing", team_only=True))
    return out


def attach_memory(findings: list[dict[str, Any]], memories: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for f in findings:
        if mode not in f["modes"]:
            continue
        matched = [m for m in memories if set(m["topics"]) & set(f["topics"]) and (f["mem_filter"] is None or f["mem_filter"](m))]
        matched.sort(key=lambda m: -len(set(m["topics"]) & set(f["topics"])))
        matched = matched[:2]
        if f["team_only"] and not matched:
            continue
        issue = {
            "severity": f["severity"], "title": f["title"], "description": f["description"],
            "line": f["line"], "code": f["code"], "recommendation": f["recommendation"], "reason": f["reason"],
            "category": f["category"], "memory_ids": [m["id"] for m in matched],
            "source_type": "team" if matched else "general", "key": f["key"],
        }
        if matched:
            rules = " ".join(f"“{m['text']}”" for m in matched)
            issue["reason"] = f"{f['reason']} Team rule on record: {rules}"
            if f["team_title"]:
                issue["title"] = f["team_title"]
            if f["team_only"]:
                issue["recommendation"] = f"{f['recommendation']} (Per team rule: {matched[0]['text']})"
            if f["severity"] == "low":
                issue["severity"] = "medium"
        issues.append(issue)
    issues.sort(key=lambda i: (SEV_ORDER[i["severity"]], i["line"] or 0))
    for n, i in enumerate(issues, 1):
        i["id"] = f"i{n}"
    return issues


PERMISSIVE = re.compile(r"\b(may|allowed|exception|except|permitted|can use|is ok|are ok)\b", re.I)
STRICT = re.compile(r"\b(must|never|all|every|always|shall)\b", re.I)


def detect_conflicts(memories: list[dict[str, Any]], issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    active = {t for i in issues for k in [i.get("key")] for t in _issue_topics(k)}
    conflicts = []
    seen = set()
    for a in memories:
        for b in memories:
            if a is b or not (STRICT.search(a["text"]) and not PERMISSIVE.search(a["text"])):
                continue
            if not (PERMISSIVE.search(b["text"]) and not re.search(r"\bnever\b", b["text"], re.I)):
                continue
            shared = (set(a["topics"]) & set(b["topics"])) & (active or set(a["topics"]))
            shared -= {"validation"}
            if not shared:
                continue
            key = (a["id"], b["id"])
            if key in seen:
                continue
            seen.add(key)
            conflicts.append({
                "memory_ids": [a["id"], b["id"]], "topic": sorted(shared)[0],
                "explanation": f"Two team conventions appear relevant and they point in different directions. "
                               f"Rule A says: “{a['text']}” Rule B says: “{b['text']}” "
                               f"RepoMind will not silently pick one — decide whether B is an approved exception to A.",
            })
    return conflicts


def _issue_topics(key: str | None) -> set[str]:
    return {
        "sql_injection": {"sql_safety", "database_access"}, "raw_sql": {"sql_safety", "database_access"},
        "repo_violation": {"database_access"}, "route_not_thin": {"api_thin", "database_access"},
        "sql_timeout": {"sql_safety"}, "debug_output": {"logging"}, "sensitive_logging": {"sensitive_logging"},
        "async_error": {"error_handling"}, "missing_auth": {"auth"},
    }.get(key or "", set())


def local_review(p: Parsed, memories: list[dict[str, Any]], mode: str, memory_enabled: bool) -> dict[str, Any]:
    issues = attach_memory(run_detectors(p), memories if memory_enabled else [], mode)
    return {"issues": issues, "summary": summarize(issues, memories if memory_enabled else [])}


def summarize(issues: list[dict[str, Any]], memories: list[dict[str, Any]]) -> str:
    if not issues:
        s = "No significant issues found."
        if memories:
            s += f" Checked against {len(memories)} relevant team convention{'s' if len(memories) != 1 else ''}."
        return s
    team = sum(1 for i in issues if i["source_type"] == "team")
    s = f"{len(issues)} issue{'s' if len(issues) != 1 else ''} found ({_sev_counts(issues)})."
    if team:
        s += f" {team} based on your team's remembered conventions."
    return s
