"""Unit tests for the five pain-point features (pure logic, no FastAPI or network). Run from backend/:  pytest -q"""
from app import insights as ins


def issue(title, sev="medium", line=None, mids=None):
    return {"title": title, "severity": sev, "line": line, "memory_ids": mids or []}


# 1. slow reviews -> instant verdict + time saved
def test_verdict_levels():
    assert ins.verdict([issue("x", "critical")])["status"] == "blocked"
    assert ins.verdict([issue("x", "high")])["status"] == "changes"
    assert ins.verdict([issue("x", "low")])["status"] == "ready"
    assert ins.verdict([])["reason"] == "No issues found"


def test_minutes_saved_uses_stated_assumptions():
    assert ins.minutes_saved(0) == ins.BASE_REVIEW_MIN
    assert ins.minutes_saved(3) == ins.BASE_REVIEW_MIN + 3 * ins.PER_ISSUE_MIN
    assert "Estimate" in ins.ASSUMPTIONS["note"]


# 2. repeat mistakes
def test_fingerprint_ignores_case_and_hyphens():
    assert ins.fingerprint("Hard\u2011coded password") == ins.fingerprint("Hardcoded Password")


def test_repeat_counts_are_per_distinct_pr_and_ignore_same_code():
    a, b = "print(1)", "print(2)"
    reviews = [
        {"code_diff": a, "issues": [issue("Print used")]},
        {"code_diff": a, "issues": [issue("Print used")]},  # same PR re-reviewed: still one PR
        {"code_diff": b, "issues": [issue("Print used")]},
    ]
    assert ins.repeat_counts(reviews, ins.code_hash("something else"))[ins.fingerprint("Print used")] == 2
    assert ins.repeat_counts(reviews, ins.code_hash(a))[ins.fingerprint("Print used")] == 1  # the current PR is excluded


def test_annotate_suggests_rule_only_when_repeated_and_uncovered():
    issues = [issue("Print used"), issue("Print used", mids=["m1"]), issue("Other")]
    ins.annotate_repeats(issues, {ins.fingerprint("Print used"): 2})
    assert [i["suggest_rule"] for i in issues] == [True, False, False]
    assert [i["seen_before"] for i in issues] == [2, 2, 0]


def test_recurring_needs_two_distinct_prs():
    reviews = [{"code_diff": "a", "issues": [issue("A", "high"), issue("B")]}, {"code_diff": "b", "issues": [issue("A", "critical")]}]
    rows = ins.recurring(reviews)
    assert len(rows) == 1 and rows[0]["title"] == "A" and rows[0]["prs"] == 2 and rows[0]["severity"] == "critical"


# 3. knowledge stays
def test_playbook_keeps_reason_owner_and_marks_missing_reason():
    mems = [
        {"text": "Never log tokens.", "category": "Security", "owner": "Priya", "reason": "Leaked once.", "times_applied": 2, "created_at": "2026-01-02T00:00:00"},
        {"text": "Use pytest.", "category": "Testing", "times_applied": 0},
    ]
    md = ins.playbook_markdown(mems, "2026-09-28T00:00:00")
    assert "## Security (1)" in md and "Why: Leaked once." in md and "Taught by: Priya" in md
    assert "not recorded yet" in md
    assert ins.knowledge_stats(mems) == {"rules": 2, "with_reason": 1, "with_owner": 1, "contributors": ["Priya"], "applied_rules": 1}


def test_playbook_empty():
    assert "No rules have been taught yet" in ins.playbook_markdown([])


# 4. consistency
def test_scorecard_marks_violated_rules_and_scores():
    mems = [{"id": "m1", "text": "r1", "category": "Logging"}, {"id": "m2", "text": "r2", "category": "Testing"}]
    sc = ins.scorecard(mems, [issue("x", mids=["m1"])])
    assert (sc["total"], sc["violated"], sc["passed"], sc["score"]) == (2, 1, 1, 50)
    assert [x["status"] for x in sc["items"]] == ["violated", "passed"]
    assert ins.scorecard([], [])["score"] is None


def test_consistency_states():
    assert ins.consistency(None, [issue("A")])["status"] == "first_review"
    assert ins.consistency([issue("A", line=3)], [issue("A renamed", line=3)])["status"] == "consistent"  # same line = same finding
    d = ins.consistency([issue("A", line=1)], [issue("B", line=9)], "rev_1")
    assert d["status"] == "differs" and d["missing"] == ["A"] and d["added"] == ["B"] and d["previous_review_id"] == "rev_1"


# 5. generic vs team-aware
def test_compare_delta_separates_team_only_upgraded_and_generic_only():
    plain = [issue("Hardcoded password", line=1), issue("Print leaks password", line=2)]
    mem = [issue("Hard-coded password", line=1, mids=["m1"]), issue("Missing type hints", line=5, mids=["m2"])]
    d = ins.compare_delta(plain, mem)
    assert d["shared"] == 1 and d["upgraded"] == 1
    assert [x["title"] for x in d["team_only"]] == ["Missing type hints"]
    assert [x["title"] for x in d["generic_only"]] == ["Print leaks password"]


def test_build_impact_counts_distinct_prs_and_team_findings():
    rev = lambda code, mem, issues, **kw: {"code_diff": code, "memory_enabled": mem, "issues": issues, "latency_ms": 2000, **kw}
    reviews = [
        rev("a", False, [issue("X", "critical")]),
        rev("a", True, [issue("X", "critical", mids=["m1"]), issue("Y", "high", mids=["m2"])], scorecard={"score": 60}, consistency={"status": "consistent"}),
        rev("b", True, [issue("X", "critical", mids=["m1"])], scorecard={"score": 80}, consistency={"status": "differs"}),
    ]
    out = ins.build_impact(reviews)
    assert out["speed"]["distinct_prs"] == 2 and out["speed"]["avg_review_seconds"] == 2.0
    assert out["speed"]["est_minutes_saved"] == ins.minutes_saved(2) + ins.minutes_saved(1)
    assert out["repeat"]["recurring"][0]["prs"] == 2
    assert out["consistency"] == {"avg_compliance": 70, "scored_reviews": 2, "repeat_reviews": 2, "repeat_consistent": 1}
    assert out["generic"] == {"memory_reviews": 2, "team_specific_findings": 3, "general_findings": 0}
