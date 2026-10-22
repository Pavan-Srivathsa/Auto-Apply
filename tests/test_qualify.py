"""Am I qualified, and why not: levels per requirement, reasons, a verdict
counted in code, and a not-qualified job that waits instead of approving."""
import json

import pytest

from tailor import brief as briefs
from tailor import jev, llm, qualify

BRIEF = briefs.Brief(role="Platform Engineer", requirements=[
    briefs.Requirement(text="TypeScript backend experience", kind="must"),
    briefs.Requirement(text="Go, Rust or Zig in production", kind="must"),
    briefs.Requirement(text="Observability tooling", kind="must", evidence="correlates logs, metrics, traces"),
    briefs.Requirement(text="Python services", kind="must", evidence="asynchronous Python services"),
    briefs.Requirement(text="CNCF projects", kind="nice"),
])
RESUME = r"\resumeItem{Built asynchronous Python services and an agent that correlates logs, metrics, traces}"


@pytest.fixture(autouse=True)
def no_jev(monkeypatch):
    for name in (jev.API_KEY_ENV, "JEV_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def items(*levels):
    return [qualify.Item(text=r.text, kind=r.kind, level=lv) for r, lv in zip(BRIEF.requirements, levels)]


def test_the_verdict_is_counted_in_code():
    call, summary = qualify.verdict(items("missing", "missing", "partly", "meets", "missing"), [])
    assert call == "not_qualified" and summary.startswith("Not qualified: 2 of 4 required items missing (TypeScript")
    assert qualify.verdict(items("missing", "meets", "meets", "meets", "missing"), [])[0] == "stretch"
    assert qualify.verdict(items("partly", "partly", "meets", "meets", "missing"), [])[0] == "stretch"
    assert qualify.verdict(items("meets", "partly", "meets", "meets", "missing"), [])[0] == "qualified"
    assert qualify.verdict(items("missing", "partly", "partly", "partly", "missing"), [])[0] == "not_qualified"
    hard = qualify.verdict(items("meets", "meets", "meets", "meets", "meets"), ["experience: 7+ years required"])
    assert hard == ("not_qualified", "Not qualified: experience: 7+ years required")


def test_hard_screen_flags_are_blockers_and_soft_ones_are_not():
    screen = {"flags": [{"category": "experience", "severity": "hard", "reason": "7+ years required"},
                        {"category": "location", "severity": "soft", "reason": "prefers NYC"}]}
    assert qualify.blockers(screen) == ["experience: 7+ years required"]
    assert qualify.blockers(None) == []


def test_jev_rates_the_levels_and_the_text_model_writes_why(monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "key")
    seen = {}

    def ask(state, questions, **kw):
        seen["state"] = state
        probs = [{"0": .9, "1": .1, "2": 0}, {"0": .37, "1": .62, "2": .01}, {"0": .05, "1": .9, "2": .05},
                 {"0": 0, "1": .3, "2": .7}, {"0": .5, "1": .5, "2": 0}]
        return {f"r{i}": {"type": "score", "score": 1.0, "confidence": 0.9, "probabilities": p}
                for i, p in enumerate(probs)}

    def complete(system, user, **kw):
        seen["levels_given"] = '"level": "partly"' in user
        return "```json\n" + json.dumps({"requirements": [
            {"i": 0, "reason": "Python and C++ in production; no TypeScript."},
            {"i": 1, "reason": "No Go, Rust or Zig."},
            {"i": 2, "reason": "An incident agent over logs and traces, not an observability platform."},
            {"i": 4, "reason": "No CNCF work."}]}) + "\n```"

    monkeypatch.setattr(jev, "ask", ask)
    monkeypatch.setattr(llm, "complete", complete)
    a = qualify.assess(BRIEF, RESUME, "stories", screen=None)
    assert [i.level for i in a.items] == ["missing", "missing", "partly", "meets", "missing"]
    assert a.verdict == "not_qualified" and a.judge == "jev" and seen["levels_given"]
    assert a.items[2].reason.startswith("An incident agent") and a.items[3].reason == ""   # no reason for "meets"
    assert "resumeItem" not in json.dumps(seen["state"]["sources"])                          # plain text, labelled


def test_without_a_key_the_text_model_rates_and_explains(monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda system, user, **kw: "```json\n" + json.dumps({"requirements": [
        {"i": i, "level": lv, "reason": f"r{i}"} for i, lv in enumerate(["meets", "meets", "partly", "meets", "missing"])
    ]}) + "\n```")
    a = qualify.assess(BRIEF, RESUME)
    assert a.verdict == "qualified" and [i.reason for i in a.items][:3] == ["", "", "r2"]


def test_a_not_qualified_job_waits_instead_of_approving(tmp_path, monkeypatch):
    import pipeline
    from server.models import Job
    monkeypatch.setattr(pipeline.settings, "auto_fill", lambda: True)
    monkeypatch.setattr(pipeline, "tell_tab", lambda job, state, note: None)
    started = []
    monkeypatch.setattr(pipeline.runner, "start_fill", lambda job_id: started.append(job_id))
    (tmp_path / "screen.json").write_text(json.dumps({"verdict": "ok"}))
    qualify.save(tmp_path, qualify.Assessment(verdict="not_qualified", summary="Not qualified: x"))
    assert pipeline.auto_approve(tmp_path, Job(url="https://x/1")) is False and started == []


def test_doubt_about_a_named_skill_counts_against_it():
    assert qualify.level_of({"score": 0.64, "probabilities": {"0": 0.37, "1": 0.62, "2": 0.01}}) == "missing"
    assert qualify.level_of({"score": 1.6, "probabilities": {"0": 0.0, "1": 0.39, "2": 0.61}}) == "meets"
    assert qualify.level_of({"score": 1.28, "probabilities": {"0": 0.01, "1": 0.7, "2": 0.29}}) == "partly"
    assert qualify.level_of({"score": 1.6}) == "meets" and qualify.level_of({"score": 0.5}) == "missing"
