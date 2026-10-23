"""Career intelligence on the review page: each job's outcome timeline, the
person's own events, and the funnel, calibration and insight report
(career/). Read-only on jobs: recording an event never changes a status."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from career import analytics
from career import events as ev
from career import snapshot
from career import stories as story_bank

from . import queue

router = APIRouter(prefix="/career", tags=["career"])


class NewEvent(BaseModel):
    event: str
    at: str = ""
    note: str = ""
    reason: str = ""
    basis: str = ""


def _replies(job_id: str) -> list:
    try:
        from outreach import store
        return store.get(job_id).replies
    except Exception:  # noqa: BLE001
        return []


@router.get("/vocabulary")
def vocabulary() -> dict:
    return {"stages": ev.STAGES, "outcomes": ev.OUTCOMES, "reasons": ev.REASONS, "bases": ev.BASES,
            "quiet_days": ev.QUIET_DAYS, "min_n": analytics.MIN_N}


@router.get("/jobs/{job_id}")
def job_events(job_id: str) -> dict:
    job = queue.get(job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    app_dir = Path(job.app_dir) if job.app_dir else None
    timeline = ev.timeline(job, app_dir, _replies(job_id))
    return {"events": [e.__dict__ for e in timeline], "stage": ev.stage_reached(timeline),
            "outcome": ev.outcome(timeline), "snapshot": _public(snapshot.load(job_id))}


def _public(frozen: Optional[dict]) -> Optional[dict]:
    return {k: v for k, v in frozen.items() if k != "posting_text"} if frozen else None


@router.post("/jobs/{job_id}")
def add_event(job_id: str, body: NewEvent) -> dict:
    if queue.get(job_id) is None:
        raise HTTPException(404, "no such job")
    try:
        ev.record(ev.Event(job_id=job_id, event=body.event, at=body.at or date.today().isoformat(),
                           source="user", note=body.note[:500], reason=body.reason, basis=body.basis))
    except ev.EventError as exc:
        raise HTTPException(422, str(exc)) from exc
    return job_events(job_id)


@router.get("/report")
def report(days: Optional[int] = None) -> dict:
    return analytics.report(days=days)


@router.get("/stories")
def stories() -> dict:
    return {"stories": [story_bank.to_dict(s) for s in story_bank.bank()],
            "suggestions": [story_bank.to_dict(s) for s in story_bank.suggestions()]}


class Confirm(BaseModel):
    verified: bool = True


@router.post("/stories/{slug}/confirm")
def confirm_story(slug: str, body: Confirm) -> dict:
    if slug not in {s.slug for s in story_bank.bank()}:
        raise HTTPException(404, "no such story")
    return story_bank.confirm(slug, body.verified)


@router.get("/jobs/{job_id}/prep")
def prep(job_id: str, refresh: bool = False) -> dict:
    try:
        return story_bank.prep(job_id, refresh)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/skills")
def skills_view(rebuild: bool = True) -> dict:
    """The skills employers ask for, the person's gaps, and the skills that
    come together per role family (career/skills.py). Rebuilt from the
    judged roles on every read; it takes well under a second."""
    from career import skills
    return skills.build() if rebuild else (skills.load() or skills.build())


@router.get("/tracker")
def tracker_view() -> dict:
    """Every job in Autopilot with its dates, stage, outcome and outreach."""
    from career import tracker
    rows = tracker.rows()
    return {"applied": [r for r in rows if r["applied"]], "in_autopilot": [r for r in rows if not r["applied"]]}
