"""Outcome tracking and analytics: events say how they are known, silence is
never a rejection, small samples get no rate, a recommendation needs two
groups whose intervals do not overlap, and snapshots are frozen."""
import json

import pytest
from fastapi.testclient import TestClient

from archive import store
from career import analytics, events as ev, snapshot
from server import queue
from server.models import Job, Status


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    qpath = tmp_path / "queue.json"
    monkeypatch.setattr(queue, "QUEUE_PATH", qpath)
    monkeypatch.setattr(queue, "LOCK_PATH", qpath.with_suffix(".lock"))
    monkeypatch.setattr(store, "APPLICATIONS", tmp_path / "applications")
    monkeypatch.setattr(store, "INDEX_PATH", tmp_path / "applications" / "index.csv")
    monkeypatch.setenv("AUTOPILOT_OUTCOMES", str(tmp_path / "outcomes.jsonl"))
    monkeypatch.setenv("AUTOPILOT_SNAPSHOTS", str(tmp_path / "snaps"))
    monkeypatch.setenv("OUTREACH_STATE", str(tmp_path / "outreach.json"))


def submitted_job(title="ML Engineer", company="Acme", days_ago=40, note="submitted by hand", fit=None, url=None):
    job, _ = queue.add(Job(url=url or f"https://example.com/{title}/{company}", title=title, company=company))
    app_dir = store.create(job)
    store.write(app_dir, "posting.md", "# ML Engineer\nWe want PyTorch.\n")
    store.write(app_dir, "resume.tex", "tex")
    if fit:
        store.write(app_dir, "qualify.json", json.dumps(fit))
    store.set_status(app_dir, Status.SUBMITTED, note)
    when = f"2026-{9 if days_ago > 25 else 10:02d}-01T10:00:00+00:00"
    history = json.loads((app_dir / "status.json").read_text())
    history["history"][-1]["at"] = when
    (app_dir / "status.json").write_text(json.dumps(history))
    queue.update(job.id, app_dir=str(app_dir), status=Status.SUBMITTED)
    return queue.get(job.id), app_dir


def test_an_event_must_say_how_it_is_known_and_use_the_vocabulary():
    with pytest.raises(ev.EventError):
        ev.record(ev.Event("j1", "interviewed", "2026-10-01"))
    with pytest.raises(ev.EventError, match="must say how"):
        ev.record(ev.Event("j1", "response", "2026-10-01", source="inferred"))
    with pytest.raises(ev.EventError, match="reason belongs"):
        ev.record(ev.Event("j1", "screen", "2026-10-01", reason="coding_interview"))
    e = ev.record(ev.Event("j1", "rejected", "2026-10-01", reason="coding_interview"))
    assert e.basis == "unknown"                     # a reason with no basis is not "confirmed"


def test_applied_is_inferred_from_the_status_history_and_silence_is_never_a_rejection():
    job, app_dir = submitted_job(days_ago=40)
    timeline = ev.timeline(job, app_dir)
    applied = next(e for e in timeline if e.event == "applied")
    assert applied.source == "inferred" and "you marked it" in applied.how
    state = ev.outcome(timeline, today="2026-10-22")
    assert state["state"] == "quiet" and "not a rejection" in state["how"]
    assert not any(e.event == "rejected" for e in timeline)
    assert ev.outcome(timeline, today="2026-09-10")["state"] == "in_flight"


def test_a_confirmation_page_is_a_different_kind_of_evidence_from_a_click():
    job, app_dir = submitted_job(note="confirmation seen by the watcher: “Thanks for applying”")
    applied = next(e for e in ev.timeline(job, app_dir) if e.event == "applied")
    assert "saw a confirmation" in applied.how


def test_a_small_group_gets_no_rate_and_intervals_come_with_every_rate():
    assert analytics.rate(2, 3)["rate"] is None and "below" in analytics.rate(2, 3)["note"]
    r = analytics.rate(5, 10)
    assert r["rate"] == 0.5 and r["ci95"][0] < 0.5 < r["ci95"][1]


def test_a_recommendation_needs_two_large_groups_with_separate_intervals(monkeypatch):
    def row(family, responded, n):
        return {"job_id": f"{family}{n}", "company": "x", "title": "t", "applied": True, "discovered_at": "2026-09-01",
                "stage": "response" if responded else "applied", "outcome": {"state": "in_progress" if responded else "quiet"},
                "events": [], "fit_score": 80, "fit_band": "80-100", "fit_verdict": "qualified", "role_family": family,
                "seniority": "unstated", "channel": "scout", "resume_base": "ml", "location": "remote",
                "screen_verdict": "ok", "skills_missing": [], "backfilled": False}
    few = [row("inference", True, i) for i in range(3)] + [row("research", False, i) for i in range(3)]
    assert analytics.recommendations(few)[0]["dimension"] == ""                     # nothing grounded
    many = [row("inference", True, i) for i in range(12)] + [row("research", False, i) for i in range(12)]
    recs = [r for r in analytics.recommendations(many) if r["dimension"] == "role_family"]
    assert recs and "12 of 12" in recs[0]["text"] and "not a cause" in recs[0]["text"]


def test_a_snapshot_is_frozen_at_submission_and_never_rewritten():
    fit = {"verdict": "qualified", "items": [{"text": "PyTorch", "kind": "must", "level": "meets"},
                                             {"text": "Go", "kind": "must", "level": "missing"}]}
    job, app_dir = submitted_job(fit=fit)
    first = snapshot.take(job, app_dir)
    assert first["fit_score"] == 50 and first["skills_missing"] == ["Go"] and first["fit_policy"] == snapshot.FIT_POLICY
    (app_dir / "qualify.json").unlink()
    store.write(app_dir, "qualify.json", json.dumps({"verdict": "not_qualified", "items": []}))
    assert snapshot.take(job, app_dir)["fit_verdict"] == "qualified"            # the first is the record


def test_recording_an_event_never_changes_the_jobs_status():
    from server.app import app
    job, _ = submitted_job()
    queue.update(job.id, status=Status.FILLED)
    client = TestClient(app)
    out = client.post(f"/career/jobs/{job.id}", json={"event": "screen", "note": "recruiter call"}).json()
    assert out["stage"] == "screen" and queue.get(job.id).status == Status.FILLED
    assert client.post(f"/career/jobs/{job.id}", json={"event": "hired!"}).status_code == 422
    report = client.get("/career/report").json()
    assert report["funnel"]["counts"]["screen"] == 1


def test_the_role_family_and_the_fit_band():
    assert snapshot.role_family("Inference Engineer, Model Serving") == "inference"
    assert snapshot.role_family("Machine Learning Engineer") == "ml"
    assert snapshot.role_family("Forward Deployed Engineer") == "forward_deployed"
    assert snapshot.band(85) == "80-100" and snapshot.band(None) == "unscored"
