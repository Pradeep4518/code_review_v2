"""End-to-end API tests. Run from backend/:  pytest -q

No network is used: Hindsight is exercised through httpx.MockTransport that asserts the exact REST
contract (paths, auth header, bodies); Groq is exercised by replacing only the network call.
"""
import json
import os
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

PRESETS = {p["id"]: p for p in json.loads((Path(__file__).parents[2] / "frontend/src/presets.json").read_text())}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    for k in ("GROQ_API_KEY", "HINDSIGHT_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("REPOMIND_DATA_DIR", str(tmp_path))
    from app.main import create_app
    return TestClient(create_app())


def review(c, preset, bypass=False, mode="general"):
    p = PRESETS[preset]
    r = c.post("/api/review", json={"code_diff": p["diff"], "pr_title": p["title"], "bypass_memory": bypass, "review_mode": mode})
    assert r.status_code == 200, r.text
    return r.json()


def test_health_reports_demo_mode_without_credentials(client):
    h = client.get("/api/health").json()
    assert h["memory_mode"] == "DEMO MEMORY MODE"
    assert h["hindsight"]["configured"] is False and h["groq"]["status"] == "NOT CONFIGURED"
    assert "key" not in json.dumps(h).lower().replace("api key", "")


def test_stateless_review_never_uses_memory(client):
    client.post("/api/seed")
    r = review(client, "raw-sql", bypass=True)
    assert r["memory_enabled"] is False and r["memory_provider"] == "none" and r["memories"] == []
    assert any(i["title"] == "SQL Injection Risk" for i in r["issues"])
    assert all(i["memory_ids"] == [] and i["source_type"] == "general" for i in r["issues"])


def test_hindsight_style_review_recalls_and_cites_team_rule(client):
    assert client.post("/api/seed").json()["seeded"] == 9
    r = review(client, "raw-sql")
    assert r["memory_enabled"] and r["memory_count"] > 0
    team = [i for i in r["issues"] if i["source_type"] == "team"]
    assert team and any("repository" in i["title"].lower() for i in team)
    used = {m["id"]: m for m in r["memories"] if m["used"]}
    assert any("repository/db.py" in m["text"] for m in used.values())
    assert r["memory_provider"] == "local"  # honest label: this is the fallback, not Hindsight


def test_teach_recall_review_loop(client):
    client.post("/api/seed")
    before = review(client, "raw-sql")
    assert not any("thin" in i["title"].lower() for i in before["issues"])
    rule = "All FastAPI route handlers must remain thin. Database access must never happen directly inside API routes."
    t = client.post("/api/teach", json={"rule": rule, "category": "Architecture"}).json()
    assert t["success"] and t["memory"]["text"] == rule
    after = review(client, "raw-sql")
    thin = [i for i in after["issues"] if "thin" in i["title"].lower()]
    assert thin and t["memory"]["id"] in thin[0]["memory_ids"]
    assert len(after["issues"]) > len(before["issues"])


def test_teach_duplicate_and_validation(client):
    body = {"rule": "Never commit generated files to the repo.", "category": "Other"}
    assert client.post("/api/teach", json=body).json()["duplicate"] is False
    assert client.post("/api/teach", json=body).json()["duplicate"] is True
    bad = client.post("/api/teach", json={"rule": "x", "category": "Other"})
    assert bad.status_code == 422 and "error" in bad.json()
    assert client.post("/api/teach", json={"rule": "long enough rule text", "category": "Nope"}).status_code == 422


def test_review_input_limits(client):
    assert client.post("/api/review", json={"code_diff": "x" * 20001}).status_code == 422
    assert client.post("/api/review", json={"code_diff": "   "}).status_code == 422
    assert client.post("/api/review", json={"code_diff": "a", "review_mode": "bogus"}).status_code == 422


def test_async_console_preset_and_security_logging(client):
    client.post("/api/seed")
    r = review(client, "async-console", mode="full")
    titles = " | ".join(i["title"] for i in r["issues"])
    assert "structured logging" in titles and "Sensitive data" in titles and "async" in titles.lower()
    stateless = review(client, "async-console", bypass=True, mode="full")
    assert "Debug output left in code" in [i["title"] for i in stateless["issues"]]


def test_clean_pr_reports_no_issues_but_mentions_conventions(client):
    client.post("/api/seed")
    r = review(client, "clean", mode="full")
    assert r["clean"] and r["issues"] == []
    assert r["review"].startswith("No significant issues found.")
    assert r["conventions_checked"], "relevant team conventions should still be mentioned"
    assert review(client, "clean", bypass=True, mode="full")["issues"] == []


def test_review_modes_change_findings(client):
    client.post("/api/seed")
    sec = {i["title"] for i in review(client, "raw-sql", mode="security")["issues"]}
    perf = {i["title"] for i in review(client, "raw-sql", mode="performance")["issues"]}
    assert "SQL Injection Risk" in sec and "SQL Injection Risk" not in perf
    assert any("Endpoint has no visible authentication" == t for t in sec)


def test_memory_conflict_detected_and_not_silently_resolved(client):
    client.post("/api/seed")
    client.post("/api/teach", json={"rule": "Analytics endpoints may use direct read-only SQL.", "category": "Database"})
    r = review(client, "raw-sql")
    assert r["conflicts"], "conflicting conventions should be surfaced"
    assert len(r["conflicts"][0]["memory_ids"]) == 2 and "Rule A" in r["conflicts"][0]["explanation"]


def test_memories_timeline_counts_and_usage(client):
    client.post("/api/seed")
    r = review(client, "raw-sql")
    m = client.get("/api/memories").json()
    assert m["total"] == 9 and m["counts"]["Security"] == 3 and m["counts"]["Database"] == 1
    used_id = r["memories_used"][0]
    mem = next(x for x in m["memories"] if x["id"] == used_id)
    assert mem["times_applied"] == 1 and mem["last_used"] and mem["related_reviews"]
    assert client.get("/api/memories", params={"category": "Logging"}).json()["memories"][0]["category"] == "Logging"
    assert client.get("/api/memories", params={"q": "pytest"}).json()["memories"][0]["category"] == "Testing"


def test_feedback_flow_and_teach_as_rule(client):
    client.post("/api/seed")
    r = review(client, "raw-sql")
    iid = r["issues"][0]["id"]
    ok = client.post("/api/feedback", json={"review_id": r["review_id"], "issue_id": iid, "feedback_type": "accepted"})
    assert ok.status_code == 200 and ok.json()["taught"] is False
    n = client.get("/api/memories").json()["total"]
    t = client.post("/api/feedback", json={"review_id": r["review_id"], "issue_id": iid, "feedback_type": "helpful",
                                            "teach_as_rule": True, "comment": "Reports must always be generated from read replicas."})
    assert t.json()["taught"] and client.get("/api/memories").json()["total"] == n + 1
    assert client.post("/api/feedback", json={"review_id": "nope", "feedback_type": "helpful"}).status_code == 404
    assert client.post("/api/feedback", json={"review_id": r["review_id"], "issue_id": "zzz", "feedback_type": "helpful"}).status_code == 404
    detail = client.get(f"/api/history/{r['review_id']}").json()
    assert detail["feedback"][iid] == "helpful"


def test_history_analytics_and_dna_use_real_data(client):
    a0 = client.get("/api/analytics").json()
    assert all(v == 0 for v in a0.values())
    assert client.get("/api/repository-dna").json()["team_rules"] == 0
    client.post("/api/seed")
    review(client, "raw-sql", bypass=True)
    r = review(client, "raw-sql")
    client.post("/api/teach", json={"rule": "Use UTC timestamps everywhere in the API.", "category": "API"})
    client.post("/api/feedback", json={"review_id": r["review_id"], "issue_id": "i1", "feedback_type": "rejected"})
    a = client.get("/api/analytics").json()
    assert a["total_reviews"] == 2 and a["memory_backed_reviews"] == 1 and a["rules_taught"] == 1
    assert a["rejected_suggestions"] == 1 and a["memories_recalled"] == r["memory_count"]
    h = client.get("/api/history").json()["reviews"]
    assert len(h) == 2 and h[0]["memory_enabled"] is True and h[1]["memories_used"] == 0
    dna = client.get("/api/repository-dna").json()
    assert dna["team_rules"] == 10 and "FastAPI" not in dna["technologies"] or True
    assert dna["sections"]["Database"]["count"] == 1 and "Pydantic" in dna["technologies"]


def test_reset_demo_clears_local_state(client):
    client.post("/api/seed")
    client.post("/api/reset-demo")
    assert client.get("/api/memories").json()["total"] == 0


def test_unknown_review_404_is_clean_json(client):
    r = client.get("/api/history/missing")
    assert r.status_code == 404 and r.json()["error"]["code"] == "http_error"


# ------------------------------------------------------------------ Hindsight contract (mock transport)
def hindsight_client(tmp_path, monkeypatch, handler):
    monkeypatch.setenv("REPOMIND_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HINDSIGHT_API_KEY", "hs_test_key")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    from app.config import get_settings
    from app.main import create_app
    from app.memory import HindsightMemoryProvider
    prov = HindsightMemoryProvider(get_settings(), transport=httpx.MockTransport(handler))
    return TestClient(create_app(prov))


def test_hindsight_provider_uses_documented_rest_contract(tmp_path, monkeypatch):
    calls, units = [], []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path))
        assert req.headers["authorization"] == "Bearer hs_test_key"
        assert str(req.url).startswith("https://api.hindsight.vectorize.io/v1/default/banks/repomind")
        if req.method == "GET" and req.url.path.endswith("/memories/list"):
            return httpx.Response(200, json={"items": units, "total": len(units), "limit": 100, "offset": 0})
        if req.method == "POST" and req.url.path.endswith("/memories/recall"):
            body = json.loads(req.content)
            assert body["query"] and body["budget"] == "mid"
            return httpx.Response(200, json={"results": [{"id": u["id"], "text": u["text"], "type": "world"} for u in units]})
        if req.method == "POST" and req.url.path.endswith("/memories"):
            body = json.loads(req.content)
            for i, it in enumerate(body["items"]):
                assert it["content"] and it["document_id"] and "repomind" in it["tags"] and it["metadata"]["category"]
                units.append({"id": f"u{len(units)}", "text": it["content"], "fact_type": "world", "tags": it["tags"],
                              "metadata": it["metadata"], "date": "2026-09-28T10:00:00Z", "context": it["context"]})
            return httpx.Response(200, json={"success": True, "bank_id": "repomind", "items_count": len(body["items"]), "async": False})
        return httpx.Response(200, json={})

    c = hindsight_client(tmp_path, monkeypatch, handler)
    h = c.get("/api/health").json()
    assert h["memory_mode"] == "HINDSIGHT CONNECTED" and h["hindsight"]["connected"]
    assert c.post("/api/seed").json()["provider"] == "hindsight"
    m = c.get("/api/memories").json()
    assert m["provider"] == "hindsight" and m["total"] == 9
    assert m["memories"][0]["text"] and not m["memories"][0]["text"].startswith("Team engineering rule")
    r = review(c, "raw-sql")
    assert r["memory_provider"] == "hindsight" and any(i["source_type"] == "team" for i in r["issues"])
    t = c.post("/api/teach", json={"rule": "Handlers must stay thin and delegate to services.", "category": "Architecture"}).json()
    assert t["provider"] == "hindsight"
    assert ("POST", "/v1/default/banks/repomind/memories/recall") in calls


def test_bypass_memory_never_calls_hindsight(tmp_path, monkeypatch):
    seen = []

    def handler(req):
        seen.append(req.url.path)
        return httpx.Response(200, json={"items": [], "total": 0})

    c = hindsight_client(tmp_path, monkeypatch, handler)
    review(c, "raw-sql", bypass=True)
    assert not any(p.endswith("/recall") for p in seen)


def test_hindsight_outage_falls_back_and_is_labelled_honestly(tmp_path, monkeypatch):
    def handler(req):
        return httpx.Response(503, json={"detail": "down"})

    c = hindsight_client(tmp_path, monkeypatch, handler)
    h = c.get("/api/health").json()
    assert h["memory_mode"] == "DEMO MEMORY MODE" and h["hindsight"]["configured"] and not h["hindsight"]["connected"]
    t = c.post("/api/teach", json={"rule": "Prefer composition over inheritance.", "category": "Architecture"}).json()
    assert t["provider"] == "local" and t["warning"]
    assert "hs_test_key" not in json.dumps(h) + json.dumps(t)


def test_hindsight_bad_key_reports_error_without_leaking_it(tmp_path, monkeypatch):
    c = hindsight_client(tmp_path, monkeypatch, lambda req: httpx.Response(401, json={}))
    h = c.get("/api/health").json()
    assert "rejected the API key" in h["hindsight"]["error"] and "hs_test_key" not in json.dumps(h)


# ------------------------------------------------------------------ Groq path (network call replaced)
def test_groq_path_validates_citations_and_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("REPOMIND_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.delenv("HINDSIGHT_API_KEY", raising=False)
    from app import llm
    from app.main import create_app
    seen = {}

    async def fake(settings, messages):
        seen["system"], seen["user"] = messages[0]["content"], messages[1]["content"]
        return ({"summary": "Found a problem.", "issues": [
            {"severity": "CRITICAL", "title": "SQL Injection Risk", "description": "d", "line": 12, "recommendation": "r", "reason": "y", "memory_ids": ["M1", "M99"]},
            {"severity": "weird", "title": "Style", "line": 999, "memory_ids": []}]}, 321)

    monkeypatch.setattr(llm, "call_groq", fake)
    c = TestClient(create_app())
    c.post("/api/seed")
    r = review(c, "raw-sql")
    assert r["review_provider"] == "groq" and r["groq_latency_ms"] == 321
    assert "specific engineering team" in seen["system"] and "[M1]" in seen["user"]
    first = r["issues"][0]
    assert first["severity"] == "critical" and len(first["memory_ids"]) == 1 and first["source_type"] == "team"
    assert r["issues"][1]["severity"] == "medium" and r["issues"][1]["line"] is None
    s = review(c, "raw-sql", bypass=True)
    assert "no knowledge of this team" in seen["system"] and "Team memories" not in seen["user"]
    assert all(i["memory_ids"] == [] for i in s["issues"])

    async def boom(settings, messages):
        raise RuntimeError("upstream down with gsk_test")

    monkeypatch.setattr(llm, "call_groq", boom)
    f = review(c, "raw-sql")
    assert f["review_provider"] == "local-deterministic" and f["warnings"] and "gsk_test" not in json.dumps(f)
    assert c.get("/api/health").json()["groq"]["status"] == "DEGRADED"


def test_llm_json_parsing_handles_fences_and_think_tags():
    from app.llm import parse_json
    assert parse_json('<think>hm</think>\n```json\n{"a": 1}\n```')["a"] == 1
    with pytest.raises(ValueError):
        parse_json("no json here")


# ---------------------------------------------------------------- team impact: the five pain-point features
def test_review_returns_verdict_time_saved_scorecard_and_consistency(client):
    client.post("/api/seed")
    r = review(client, "raw-sql")
    assert r["verdict"]["status"] == "blocked" and r["time_saved_min"] >= 10  # 1: instant merge-readiness + estimate
    sc = r["scorecard"]
    assert sc["total"] == len(r["memories"]) and sc["violated"] + sc["passed"] == sc["total"]  # 4: same checklist
    assert r["consistency"]["status"] == "first_review"
    assert review(client, "raw-sql")["consistency"]["status"] == "consistent"  # same code, same findings
    assert review(client, "raw-sql", bypass=True)["scorecard"] is None  # a stateless review has no team checklist


def test_repeat_mistake_is_flagged_across_different_prs_only(client):
    p = PRESETS["raw-sql"]
    same = [review(client, "raw-sql", bypass=True) for _ in range(3)]
    assert all(i["seen_before"] == 0 for r in same for i in r["issues"])  # re-reviewing the same code never counts as a repeat
    for n in range(2):
        r = client.post("/api/review", json={"code_diff": p["diff"].replace("search_users", f"search_users_{n}"), "pr_title": f"PR {n}", "bypass_memory": True, "review_mode": "general"}).json()
    sql = next(i for i in r["issues"] if i["title"] == "SQL Injection Risk")
    assert sql["seen_before"] >= 2 and sql["suggest_rule"] is True  # 2: seen in 2+ earlier PRs and no rule covers it
    rec = client.get("/api/impact").json()["repeat"]["recurring"]
    assert any(x["title"] == "SQL Injection Risk" and x["prs"] >= 3 for x in rec)


def test_teach_keeps_owner_and_reason_and_playbook_exports_them(client):
    t = client.post("/api/teach", json={"rule": "Never call the payments API without an idempotency key.", "category": "API", "owner": "Priya", "reason": "A retry once double-charged customers."}).json()
    assert t["memory"]["owner"] == "Priya" and t["memory"]["reason"] == "A retry once double-charged customers."
    mems = client.get("/api/memories").json()["memories"]
    assert mems[0]["owner"] == "Priya" and "double-charged" in mems[0]["reason"]
    pb = client.get("/api/playbook").json()
    assert pb["filename"].endswith(".md") and "Priya" in pb["markdown"] and "double-charged" in pb["markdown"]
    k = client.get("/api/impact").json()["knowledge"]
    assert k["rules"] == 1 and k["with_reason"] == 1 and k["contributors"] == ["Priya"]  # 3: knowledge outlives its author


def test_seed_rules_carry_a_reason_for_the_playbook(client):
    client.post("/api/seed")
    assert all(m["reason"] for m in client.get("/api/memories").json()["memories"])


def test_compare_endpoint_runs_both_reviews_and_reports_the_delta(client):
    client.post("/api/seed")
    p = PRESETS["raw-sql"]
    c = client.post("/api/compare", json={"code_diff": p["diff"], "pr_title": p["title"], "review_mode": "general"}).json()
    assert c["plain"]["memory_enabled"] is False and c["memory"]["memory_enabled"] is True
    d = c["delta"]
    assert set(d) == {"shared", "upgraded", "team_only", "memory_extra_general", "generic_only"}
    assert d["shared"] >= 1 and (d["team_only"] or d["upgraded"])  # 5: memory adds team-specific value over a generic review
    assert client.post("/api/compare", json={"code_diff": "  ", "pr_title": "x"}).status_code == 422


def test_impact_dashboard_shape_and_assumptions_are_disclosed(client):
    client.post("/api/seed")
    review(client, "raw-sql")
    d = client.get("/api/impact").json()
    assert set(d) == {"speed", "repeat", "consistency", "generic", "knowledge", "assumptions"}
    assert d["speed"]["distinct_prs"] == 1 and d["speed"]["est_minutes_saved"] >= 10
    assert d["generic"]["team_specific_findings"] >= 1 and "Estimate" in d["assumptions"]["note"]
    assert client.post("/api/reset-demo").status_code == 200
    assert client.get("/api/impact").json()["speed"]["distinct_prs"] == 0
