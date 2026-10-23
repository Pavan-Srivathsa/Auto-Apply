"""The bullet review on the review page: the person's accept / reject per
bullet, and putting the master's wording back for the rejected ones.

The review itself (`bullet_review.json`) is in the application folder, which
is immutable, so decisions live in `data/bullet_decisions.json`, keyed by the
folder they were made on. Applying them writes a new resume version through
`pipeline.save_resume` (every tailoring check, a compile) and reviews it
again, so the new folder carries its own review. Nothing here approves,
fills or submits anything.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import paths
from tailor import reviewer

from . import queue

router = APIRouter(prefix="/review", tags=["bullets"])
DECISIONS = ("accept", "reject", "edit", "")
_lock = threading.Lock()


def _path() -> Path:
    return Path(os.environ.get("AUTOPILOT_BULLET_DECISIONS") or paths.DATA / "bullet_decisions.json")


def _all() -> dict:
    try:
        return json.loads(_path().read_text())
    except (OSError, ValueError):
        return {}


def decisions(folder: str) -> dict[int, dict]:
    """index -> {"decision", "text"}. Older entries were the bare decision."""
    out = {}
    for k, v in _all().get(folder, {}).items():
        out[int(k)] = v if isinstance(v, dict) else {"decision": v, "text": ""}
    return out


def _write(folder: str, values: dict[int, dict]) -> None:
    with _lock:
        data = _all()
        data[folder] = {str(k): v for k, v in values.items() if v and v.get("decision")}
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1))
        tmp.replace(path)


def with_decisions(app_dir: Path) -> Optional[reviewer.Review]:
    """The folder's review with the person's decisions on it, or None."""
    review = reviewer.load(app_dir)
    if review is None:
        return None
    chosen = decisions(Path(app_dir).name)
    for b in review.bullets:
        if b.index in chosen:
            b.decision, b.edited = chosen[b.index]["decision"], chosen[b.index].get("text", "")
    return review


def detail(app_dir: Path) -> Optional[dict]:
    review = with_decisions(app_dir)
    return review.to_dict() if review else None


def _job_dir(job_id: str):
    job = queue.get(job_id)
    if job is None or not job.app_dir or not Path(job.app_dir).exists():
        raise HTTPException(404, "no application folder for this job")
    return job, Path(job.app_dir)


class Decision(BaseModel):
    decision: str
    text: str = ""            # the person's own wording, for "edit"


@router.post("/{job_id}/bullets/{index}/decision")
def decide(job_id: str, index: int, body: Decision) -> dict:
    if body.decision not in DECISIONS:
        raise HTTPException(422, f"decision must be one of {DECISIONS}")
    if body.decision == "edit" and len(body.text.split()) < 4:
        raise HTTPException(422, "an edit needs the bullet's new wording")
    _, app_dir = _job_dir(job_id)
    review = reviewer.load(app_dir)
    if review is None or not any(b.index == index for b in review.bullets):
        raise HTTPException(404, "no such bullet in this job's review")
    chosen = decisions(app_dir.name)
    chosen[index] = {"decision": body.decision, "text": body.text if body.decision == "edit" else ""}
    _write(app_dir.name, chosen)
    return {"index": index, "decision": body.decision, "review": detail(app_dir)}


@router.post("/{job_id}/bullets/apply")
def apply(job_id: str) -> dict:
    """Put the master's wording back for every rejected bullet that has one,
    and the person's wording in for every edited one, as a new resume
    version; review that version; and lock the edits to this job so no later
    automated revision writes over them."""
    import pipeline
    from tailor import profile

    job, app_dir = _job_dir(job_id)
    review = with_decisions(app_dir)
    if review is None:
        raise HTTPException(404, "this job has no bullet review")
    rejected = [b.index for b in review.bullets if b.decision == "reject" and b.original_tex]
    edits = {b.index: b.edited for b in review.bullets if b.decision == "edit" and b.edited}
    if not rejected and not edits:
        raise HTTPException(409, "nothing to apply: no rejected or edited bullet")
    tex = reviewer.revert((app_dir / "resume.tex").read_text(), review, rejected)
    tex = reviewer.edit(tex, review, edits)
    try:
        new_dir = pipeline.save_resume(job, tex)
    except Exception as exc:  # noqa: BLE001 - a check refused it; say which
        raise HTTPException(422, f"the restored resume did not pass the checks: {exc}") from exc
    master = (new_dir / "master_resume.tex").read_text()
    brief = None
    try:
        from tailor import brief as briefs
        brief = briefs.load(app_dir)
    except Exception:  # noqa: BLE001
        pass
    texts = pipeline._story_texts(profile.read_used(app_dir))
    fresh = reviewer.review(master, tex, [r.text for r in brief.requirements] if brief else [], texts)
    accepted = {b.rewritten for b in review.bullets if b.decision == "accept"}
    edited = {" ".join(t.split()) for t in edits.values()}
    carried = {b.index: {"decision": "accept", "text": ""} for b in fresh.bullets if b.rewritten in accepted}
    carried |= {b.index: {"decision": "edit", "text": b.rewritten} for b in fresh.bullets
                if " ".join(b.rewritten.split()) in edited}
    fresh.rounds = review.rounds + [{"round": "person", "outcome": f"{len(rejected)} put back to the master's "
                                                                  f"wording, {len(edits)} edited, on the review page"}]
    reviewer.save(new_dir, fresh)
    if carried:
        _write(new_dir.name, carried)
    if edits:
        by_index = {b.index: b for b in review.bullets}
        reviewer.add_locks(job.id, [{"original": by_index[i].original, "text": t} for i, t in edits.items()
                                    if by_index[i].original])
    return {"folder": new_dir.name, "restored": len(rejected), "edited": len(edits), "review": detail(new_dir)}
