"""Rule lifecycle: list who/when, edit, retire/restore, replace (supersede), delete, and newest-rule-wins.

Run from backend/:  pytest -q tests/test_rule_lifecycle.py
"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from .test_api import hindsight_client, review


@pytest.fixture()
def client(tmp_path, monkeypatch):
    for k in ("GROQ_API_KEY", "HINDSIGHT_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("REPOMIND_DATA_DIR", str(tmp_path))
    from app.main import create_app
    return TestClient(create_app())


def rules(c, status="active"):
    return c.get("/api/memories", params={"status": status}).json()


def find(c, needle, status="active"):
    return next(m for m in rules(c, status)["memories"] if needle in m["text"])


def set_created(c, mem_id, iso):
    def fn(s):
        for m in s["local_memories"]:
            if m["id"] == mem_id:
                m["created_at"] = iso
    c.app.state.svc.store.write(fn)


# ------------------------------------------------------------------ list: who gave it, when
def test_list_shows_who_gave_each_rule_and_when(client):
    client.post("/api/teach", json={"rule": "Never commit generated protobuf files.", "category": "Other", "owner": "Priya", "reason": "Noisy diffs"})
    m = find(client, "protobuf")
    assert m["given_by"] == "Priya" and m["given_at"] and m["status"] == "active" and m["history"] == []
    seeded = client.post("/api/seed").json()
    assert seeded["seeded"] == 9
    assert find(client, "repository/db.py")["given_by"] == "Seed data"


# ------------------------------------------------------------------ edit / update
def test_edit_rewrites_rule_and_reviewer_uses_new_wording(client):
    client.post("/api/seed")
    old = find(client, "repository/db.py")
    r = client.patch(f"/api/memories/{old['id']}", json={"rule": "All database access must go through repository/data_access.py.", "actor": "Asha"})
    assert r.status_code == 200 and r.json()["changed"]
    m = r.json()["memory"]
    assert "data_access.py" in m["text"] and m["edited"] and m["updated_by"] == "Asha" and m["given_by"] == "Seed data"
    assert m["history"][-1]["action"] == "edited" and m["history"][-1]["by"] == "Asha"
    assert rules(client)["total"] == 9  # edited in place, not duplicated
    rev = review(client, "raw-sql")
    cited = " ".join(i["reason"] + i["recommendation"] for i in rev["issues"])
    assert "data_access.py" in cited and "repository/db.py" not in cited


def test_edit_validation_and_duplicate_guard(client):
    client.post("/api/seed")
    a = find(client, "structured logging")
    assert client.patch(f"/api/memories/{a['id']}", json={"actor": "x"}).status_code == 422       # nothing to change
    assert client.patch(f"/api/memories/{a['id']}", json={"rule": "short"}).status_code == 422
    dup = client.patch(f"/api/memories/{a['id']}", json={"rule": find(client, "parameterized")["text"]})
    assert dup.status_code == 422 and "already says this" in dup.json()["error"]["message"]
    assert client.patch("/api/memories/nope", json={"rule": "Long enough rule text."}).status_code == 404
    same = client.patch(f"/api/memories/{a['id']}", json={"category": a["category"]}).json()
    assert same["changed"] is False


# ------------------------------------------------------------------ retire / restore
def test_retire_removes_rule_from_reviews_and_restore_brings_it_back(client):
    client.post("/api/seed")
    repo = find(client, "repository/db.py")
    r = client.post(f"/api/memories/{repo['id']}/retire", json={"actor": "Asha", "reason": "Moved to an ORM"}).json()
    assert r["memory"]["status"] == "retired" and r["memory"]["retired_by"] == "Asha" and r["memory"]["retire_reason"] == "Moved to an ORM"
    live = rules(client)
    assert live["total"] == 8 and live["inactive_total"] == 1 and all(m["id"] != repo["id"] for m in live["memories"])
    assert find(client, "repository/db.py", status="inactive")["status"] == "retired"
    assert client.get("/api/health").json()["memory_count"] == 8
    rev = review(client, "raw-sql")
    assert all("repository layer" not in i["title"] for i in rev["issues"])  # team-only finding needs the rule
    assert repo["id"] not in [m["id"] for m in rev["memories"]]
    assert "repository/db.py" not in client.get("/api/playbook").json()["markdown"]
    back = client.post(f"/api/memories/{repo['id']}/restore", json={"actor": "Asha"}).json()
    assert back["memory"]["status"] == "active" and [h["action"] for h in back["memory"]["history"]] == ["retired", "restored"]
    assert any("repository layer" in i["title"] for i in review(client, "raw-sql")["issues"])


def test_retire_is_idempotent(client):
    client.post("/api/seed")
    m = find(client, "pytest")
    client.post(f"/api/memories/{m['id']}/retire", json={})
    again = client.post(f"/api/memories/{m['id']}/retire", json={}).json()
    assert again["changed"] is False and len(again["memory"]["history"]) == 1


# ------------------------------------------------------------------ replace / supersede
def test_new_rule_supersedes_old_rule(client):
    client.post("/api/seed")
    old = find(client, "5-second statement timeout")
    r = client.post(f"/api/memories/{old['id']}/supersede", json={
        "rule": "Every SQL statement must use parameterized queries and run with a 2-second statement timeout.",
        "actor": "Asha", "reason": "Tighter SLO"}).json()
    new, gone = r["memory"], r["superseded"]
    assert gone["status"] == "superseded" and gone["superseded_by"] == new["id"] and gone["retired_by"] == "Asha"
    assert new["status"] == "active" and new["supersedes"] == old["id"] and new["given_by"] == "Asha" and new["category"] == "Security"
    assert rules(client)["total"] == 9 and find(client, "2-second")
    assert find(client, "5-second", status="inactive")["status"] == "superseded"
    rev = review(client, "raw-sql")
    text = json.dumps(rev["issues"])
    assert "2-second" in text and "5-second" not in text
    # unknown / bad requests
    assert client.post(f"/api/memories/{gone['id']}/supersede", json={"rule": "Something else entirely here."}).status_code == 422
    assert client.post(f"/api/memories/{new['id']}/supersede", json={}).status_code == 422
    assert client.post(f"/api/memories/{new['id']}/supersede", json={"rule": new["text"]}).status_code == 422


def test_supersede_with_an_existing_rule_and_restore_unlinks(client):
    client.post("/api/seed")
    old = find(client, "structured logging")
    t = client.post("/api/teach", json={"rule": "Log through the platform logger only; never print().", "category": "Logging", "owner": "Ravi"}).json()
    assert [o["id"] for o in t["related"]] == [old["id"]]                 # offered as a candidate to replace
    r = client.post(f"/api/memories/{old['id']}/supersede", json={"new_rule_id": t["memory"]["id"], "actor": "Ravi"}).json()
    assert r["superseded"]["status"] == "superseded" and r["memory"]["supersedes"] == old["id"]
    assert client.post(f"/api/memories/{old['id']}/supersede", json={"new_rule_id": old["id"]}).status_code in (404, 422)
    back = client.post(f"/api/memories/{old['id']}/restore", json={"actor": "Ravi"}).json()["memory"]
    assert back["status"] == "active" and back["superseded_by"] is None
    assert find(client, "platform logger")["supersedes"] is None


# ------------------------------------------------------------------ delete
def test_delete_removes_rule_everywhere(client):
    client.post("/api/seed")
    m = find(client, "AppError")
    r = client.delete(f"/api/memories/{m['id']}", params={"actor": "Asha"})
    assert r.status_code == 200 and r.json()["deleted"] == m["id"]
    assert rules(client)["total"] == 8 and all(x["id"] != m["id"] for x in rules(client, "all")["memories"])
    assert client.delete(f"/api/memories/{m['id']}").status_code == 404
    assert client.patch(f"/api/memories/{m['id']}", json={"rule": "Long enough rule text."}).status_code == 404
    assert "AppError" not in client.get("/api/playbook").json()["markdown"]
    client.post("/api/teach", json={"rule": m["text"], "category": "Architecture"})  # teaching it again works
    assert rules(client)["total"] == 9


# ------------------------------------------------------------------ newest rule wins
def _conflict_setup(client, permissive_is_newer):
    client.post("/api/seed")
    client.post("/api/teach", json={"rule": "Analytics endpoints may use direct read-only SQL.", "category": "Database", "owner": "Ravi"})
    strict, loose = find(client, "repository/db.py"), find(client, "Analytics endpoints")
    for m in rules(client)["memories"]:                              # everything else is clearly older than the pair under test
        set_created(client, m["id"], "2025-06-01T09:00:00+00:00")
    older, newer = "2026-01-01T09:00:00+00:00", "2026-06-01T09:00:00+00:00"
    set_created(client, strict["id"], older if permissive_is_newer else newer)
    set_created(client, loose["id"], newer if permissive_is_newer else older)
    return strict, loose


@pytest.mark.parametrize("permissive_is_newer", [True, False])
def test_reviewer_prefers_the_newest_rule_when_two_conflict(client, permissive_is_newer):
    strict, loose = _conflict_setup(client, permissive_is_newer)
    win, lose = (loose, strict) if permissive_is_newer else (strict, loose)
    r = review(client, "raw-sql")
    c = next(c for c in r["conflicts"] if set(c["memory_ids"]) == {strict["id"], loose["id"]})
    assert c["resolution"] == "newest_wins" and c["winner_id"] == win["id"] and c["loser_id"] == lose["id"]
    assert "Newest wins" in c["explanation"] and "Rule A" in c["explanation"] and "silently pick" not in c["explanation"]
    by_id = {m["id"]: m for m in r["memories"]}
    assert by_id[lose["id"]]["overridden"] and not by_id[lose["id"]]["used"] and not by_id[win["id"]]["overridden"]
    cited = {mid for i in r["issues"] for mid in i["memory_ids"]}
    assert lose["id"] not in cited                                   # the older rule is never cited
    assert lose["id"] not in [x["id"] for x in r["scorecard"]["items"]]


def test_conflict_with_the_same_date_is_still_left_open(client):
    strict, loose = _conflict_setup(client, True)
    set_created(client, loose["id"], "2026-01-01T09:00:00+00:00")   # identical to the strict rule's date
    r = review(client, "raw-sql")
    c = next(c for c in r["conflicts"] if set(c["memory_ids"]) == {strict["id"], loose["id"]})
    assert "resolution" not in c and "silently pick" in c["explanation"]


def test_editing_an_older_rule_makes_it_the_newest(client):
    strict, loose = _conflict_setup(client, permissive_is_newer=True)   # loose is newer, would win
    client.patch(f"/api/memories/{strict['id']}", json={"rule": "All database access must go through repository/db.py, no exceptions.", "actor": "Asha"})
    r = review(client, "raw-sql")
    c = next(c for c in r["conflicts"] if len(c["memory_ids"]) == 2 and c.get("resolution"))
    assert c["winner_id"] == strict["id"]


def test_retiring_the_newer_rule_ends_the_conflict(client):
    strict, loose = _conflict_setup(client, permissive_is_newer=True)
    client.post(f"/api/memories/{loose['id']}/retire", json={"actor": "Asha"})
    r = review(client, "raw-sql")
    assert not [c for c in r["conflicts"] if loose["id"] in c["memory_ids"]]
    assert any("repository layer" in i["title"] for i in r["issues"])


def test_llm_prompt_carries_rule_dates_and_newest_wins_instruction():
    from app import llm
    msgs, idmap = llm.build_messages("t", "+x = 1", "general", [{"id": "a", "category": "Security", "text": "Never log tokens.", "effective_at": "2026-09-01T10:00:00+00:00"}])
    assert "added 2026-09-01" in msgs[1]["content"] and "newer" in msgs[0]["content"] and idmap == {"M1": "a"}


# ------------------------------------------------------------------ Hindsight: delete contract
def test_hindsight_delete_uses_documented_document_endpoint(tmp_path, monkeypatch):
    calls, units = [], [{"id": "u1", "document_id": "doc-1", "text": "Team engineering rule (Other): Ship small PRs.", "fact_type": "world",
                         "metadata": {"category": "Other", "owner": "Ravi"}, "date": "2026-09-01T10:00:00Z"}]

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path))
        if req.method == "GET":
            return httpx.Response(200, json={"items": [] if ("DELETE", "/v1/default/banks/repomind/documents/doc-1") in calls else units, "total": 1})
        return httpx.Response(200, json={})

    c = hindsight_client(tmp_path, monkeypatch, handler)
    m = c.get("/api/memories").json()["memories"][0]
    assert m["given_by"] == "Ravi" and m["given_at"] == "2026-09-01T10:00:00Z"
    r = c.delete(f"/api/memories/{m['id']}", params={"actor": "Asha"}).json()
    assert r["success"] and r["warning"] is None
    assert ("DELETE", "/v1/default/banks/repomind/documents/doc-1") in calls
    assert c.get("/api/memories").json()["total"] == 0


def test_hindsight_delete_failure_still_hides_rule_and_says_so(tmp_path, monkeypatch):
    units = [{"id": "u1", "document_id": "doc-1", "text": "Ship small PRs.", "fact_type": "world", "metadata": {"category": "Other"}, "date": "2026-09-01T10:00:00Z"}]

    def handler(req):
        if req.method == "DELETE":
            return httpx.Response(500, json={})
        return httpx.Response(200, json={"items": units, "total": 1})

    c = hindsight_client(tmp_path, monkeypatch, handler)
    r = c.delete("/api/memories/u1").json()
    assert "could not delete its copy" in r["warning"]
    assert c.get("/api/memories").json()["total"] == 0               # tombstone keeps it out of the list and out of reviews


def test_hindsight_retire_and_edit_are_layered_locally(tmp_path, monkeypatch):
    units = [{"id": "u1", "document_id": "doc-1", "text": "Ship small PRs.", "fact_type": "world", "metadata": {"category": "Other"}, "date": "2026-09-01T10:00:00Z"}]
    calls = []

    def handler(req):
        calls.append(req.method)
        return httpx.Response(200, json={"items": units, "total": 1, "results": [{"id": "u1", "text": "Ship small PRs."}]})

    c = hindsight_client(tmp_path, monkeypatch, handler)
    e = c.patch("/api/memories/u1", json={"rule": "Ship PRs under 400 changed lines.", "actor": "Asha"}).json()["memory"]
    assert e["text"] == "Ship PRs under 400 changed lines." and e["edited"]
    assert c.post("/api/memories/u1/retire", json={"actor": "Asha"}).json()["memory"]["status"] == "retired"
    assert c.get("/api/memories").json()["total"] == 0 and c.get("/api/memories", params={"status": "inactive"}).json()["total"] == 0
    assert c.get("/api/memories", params={"status": "inactive"}).json()["inactive_total"] == 1
    assert "DELETE" not in calls
