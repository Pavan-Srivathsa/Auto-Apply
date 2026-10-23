"""What the app believed about a job when it was submitted, frozen.

Written once per job at submission (`server/review.mark_submitted` and
`mark_seen`) and never overwritten, so a later change to the fit policy, the
resume or the posting's page cannot rewrite the evidence the calibration
reads. A job submitted before this existed gets a snapshot built from its
folder on first read, marked `backfilled` with the date it was built.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import paths

SCHEMA = 1
# The fit policy whose verdicts these snapshots record (tailor/qualify.py and
# its thresholds). Bump it whenever the qualify rules change.
FIT_POLICY = "qualify-2026-10-22"

FAMILIES = [   # first match wins; the order is most specific first
    ("inference", r"\binference\b|\bserving\b|\bmodel optimi[sz]ation\b"),
    ("forward_deployed", r"forward[- ]deployed|\bfde\b|solutions engineer|deployment engineer"),
    ("agents", r"\bagents?\b|\bagentic\b"),
    ("evaluation", r"\beval(uation)?s?\b"),
    ("research", r"\bresearch(er)?\b|\bscientist\b(?!.*data)"),
    ("data_science", r"data scien|\banalytics\b|\banalyst\b"),
    ("ml", r"machine learning|\bml\b|\bmlops\b"),
    ("ai_engineer", r"\bai\b|\bllm\b|genai|applied ai"),
    ("backend", r"backend|back-end|platform|infrastructure|\binfra\b|distributed"),
    ("data_engineering", r"data engineer"),
    ("software", r"software|engineer|developer"),
]
SENIORITY = [("senior", r"\b(senior|sr\.?|staff|principal|lead)\b"), ("junior", r"\b(junior|jr\.?|new grad|entry|associate|i)\b"),
             ("intern", r"\bintern(ship)?\b")]


def role_family(title: str) -> str:
    low = (title or "").lower()
    return next((name for name, pattern in FAMILIES if re.search(pattern, low)), "other")


def seniority(title: str) -> str:
    low = (title or "").lower()
    return next((name for name, pattern in SENIORITY if re.search(pattern, low)), "unstated")


def fit_score(fit: Optional[dict]) -> Optional[int]:
    """0..100 from the qualify verdict: required items met count 1, partly
    count a half. None when the job has no assessment."""
    items = [i for i in (fit or {}).get("items", []) if i.get("kind") == "must"]
    if not items:
        return None
    got = sum(1.0 if i.get("level") == "meets" else 0.5 if i.get("level") == "partly" else 0.0 for i in items)
    return round(100 * got / len(items))


def band(score: Optional[int]) -> str:
    if score is None:
        return "unscored"
    return "80-100" if score >= 80 else "50-79" if score >= 50 else "0-49"


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def _json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def directory() -> Path:
    return Path(os.environ.get("AUTOPILOT_SNAPSHOTS") or paths.DATA / "career" / "snapshots")


def build(job, app_dir: Path, backfilled: bool = False) -> dict:
    from tailor import quality
    app_dir = Path(app_dir)
    fit = _json(app_dir / "qualify.json")
    screen = _json(app_dir / "screen.json")
    posting = _read(app_dir / "posting.md")
    resume = _read(app_dir / "resume.tex")
    items = fit.get("items", [])
    history = _json(app_dir / "status.json").get("history", [])
    submitted = next((h["at"] for h in history if h.get("status") == "submitted"), "")
    flags = screen.get("flags") or []
    location = next((f for f in flags if "location" in str(f.get("category", "")).lower()), None)
    return {
        "schema": SCHEMA, "job_id": job.id, "company": job.company, "title": job.title, "url": job.url,
        "channel": job.source or "unknown", "folder": app_dir.name,
        "submitted_at": submitted or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fit_policy": FIT_POLICY, "fit_verdict": fit.get("verdict", ""), "fit_score": fit_score(fit),
        "fit_summary": fit.get("summary", ""),
        "skills_matched": [i["text"] for i in items if i.get("level") == "meets"],
        "skills_partly": [i["text"] for i in items if i.get("level") == "partly"],
        "skills_missing": [i["text"] for i in items if i.get("level") == "missing"],
        "screen_verdict": screen.get("verdict", ""),
        "location_eligible": None if location is None else location.get("severity") not in ("hard",),
        "role_family": role_family(job.title), "seniority": seniority(job.title),
        "resume_base": _json(app_dir / "resume_base.json").get("base", ""),
        "resume_sha256": hashlib.sha256(resume.encode()).hexdigest() if resume else "",
        "posting_sha256": hashlib.sha256(posting.encode()).hexdigest() if posting else "",
        "posting_simhash": quality.simhash(posting) if posting else 0,
        "posting_text": posting[:20000],
        "backfilled": backfilled,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def path_for(job_id: str) -> Path:
    return directory() / f"{job_id}.json"


def take(job, app_dir: Path, backfilled: bool = False) -> dict:
    """Freeze the snapshot for this job, once. An existing one is returned
    untouched: the first is the record."""
    target = path_for(job.id)
    if target.exists():
        return json.loads(target.read_text())
    snap = build(job, app_dir, backfilled)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(snap, indent=1))
    tmp.replace(target)
    return snap


def load(job_id: str) -> Optional[dict]:
    try:
        return json.loads(path_for(job_id).read_text())
    except (OSError, ValueError):
        return None
