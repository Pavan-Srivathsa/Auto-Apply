"""The funnel, calibration, bottlenecks and recommendations, from evidence.

Honesty rules, in order:

1. An application still in flight (applied, under `events.QUIET_DAYS`, no
   later event) is not a data point; it is counted apart as in flight.
   A quiet one (past that, nothing recorded) counts as "no response yet":
   not a response, never a rejection.
2. No rate is reported for a group below `MIN_N` resolved applications;
   every rate carries its denominator and a 95% Wilson interval.
3. A recommendation compares two groups only when both clear `MIN_N` and
   their intervals do not overlap, and says so with the counts. Patterns are
   associations, not causes, and a hiring decision is not a judgement of skill.
4. Strictly advisory: nothing here changes a score, a threshold or a job.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from . import events as ev
from . import snapshot as snap

MIN_N = 5
DIMENSIONS = ("fit_band", "fit_verdict", "role_family", "seniority", "channel", "resume_base", "location", "screen_verdict")
TRANSITIONS = [("applied", "response"), ("response", "screen"), ("screen", "technical"),
               ("technical", "final"), ("final", "offer")]


def wilson(k: int, n: int, z: float = 1.96) -> Optional[tuple[float, float]]:
    if n == 0:
        return None
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3)


def rate(k: int, n: int) -> dict:
    out = {"k": k, "n": n}
    if n >= MIN_N:
        out |= {"rate": round(k / n, 3), "ci95": wilson(k, n)}
    else:
        out |= {"rate": None, "note": f"n={n} is below {MIN_N}: no rate"}
    return out


def _location(job_snap: dict, job) -> str:
    text = (job_snap or {}).get("posting_text", "")[:3000].lower()
    if "remote" in (job.title or "").lower() or "fully remote" in text or "remote (us" in text:
        return "remote"
    return "onsite_or_hybrid" if text else "unknown"


def rows(today: Optional[str] = None) -> list[dict]:
    """One row per job: its events, the stage reached, its outcome, and the
    snapshot dimensions (taken now, marked backfilled, if it had none)."""
    from server import queue
    try:
        from outreach import store as outreach_store
    except Exception:  # noqa: BLE001
        outreach_store = None
    out = []
    for job in queue.all_jobs():
        app_dir = Path(job.app_dir) if job.app_dir else None
        replies = []
        if outreach_store is not None:
            try:
                replies = outreach_store.get(job.id).replies
            except Exception:  # noqa: BLE001
                replies = []
        timeline = ev.timeline(job, app_dir, replies)
        stage = ev.stage_reached(timeline)
        state = ev.outcome(timeline, today)
        applied = ev.RANK[stage] >= ev.RANK["applied"]
        frozen = snap.load(job.id)
        if frozen is None and applied and app_dir and app_dir.exists():
            frozen = snap.take(job, app_dir, backfilled=True)
        frozen = frozen or {}
        score = frozen.get("fit_score")
        out.append({
            "job_id": job.id, "company": job.company, "title": job.title, "stage": stage, "outcome": state,
            "applied": applied, "applied_at": next((e.at[:10] for e in timeline if e.event == "applied"), ""),
            "discovered_at": (job.added_at or "")[:10],
            "events": [e.__dict__ for e in timeline],
            "fit_score": score, "fit_band": snap.band(score), "fit_verdict": frozen.get("fit_verdict") or "unscored",
            "role_family": frozen.get("role_family") or snap.role_family(job.title),
            "seniority": frozen.get("seniority") or snap.seniority(job.title),
            "channel": frozen.get("channel") or job.source or "unknown",
            "resume_base": frozen.get("resume_base") or "unknown",
            "location": _location(frozen, job), "screen_verdict": frozen.get("screen_verdict") or "unknown",
            "skills_missing": frozen.get("skills_missing", []) + frozen.get("skills_partly", []),
            "backfilled": bool(frozen.get("backfilled")),
        })
    return out


def resolved(row: dict) -> bool:
    return row["applied"] and row["outcome"]["state"] not in ("in_flight", "not_applied")


def reached(row: dict, stage: str) -> bool:
    return ev.RANK[row["stage"]] >= ev.RANK[stage]


def funnel(data: list[dict], days: Optional[int] = None) -> dict:
    """Discovered -> ... -> offer, cumulative: a job counts at every stage up
    to the furthest it reached. `days` keeps jobs discovered in that window."""
    if days:
        cutoff = (datetime.now() - timedelta(days=days)).date().isoformat()
        data = [r for r in data if r["discovered_at"] >= cutoff]
    counts = {stage: sum(1 for r in data if reached(r, stage)) for stage in ev.STAGES}
    applied = [r for r in data if r["applied"]]
    steps = []
    for before, after in TRANSITIONS:
        pool = [r for r in applied if reached(r, before) and (resolved(r) or reached(r, after))]
        steps.append({"from": before, "to": after} | rate(sum(1 for r in pool if reached(r, after)), len(pool)))
    return {"window_days": days, "jobs": len(data), "counts": counts, "conversion": steps,
            "in_flight": sum(1 for r in applied if r["outcome"]["state"] == "in_flight"),
            "quiet": sum(1 for r in applied if r["outcome"]["state"] == "quiet"),
            "resolved": sum(1 for r in applied if resolved(r))}


def by(data: list[dict], dimension: str) -> list[dict]:
    """Response, screen and interview rates per group, resolved applications only."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in data:
        if resolved(r):
            groups[str(r[dimension])].append(r)
    out = []
    for name, members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        n = len(members)
        out.append({"group": name, "n": n,
                    "response": rate(sum(reached(r, "response") for r in members), n),
                    "screen": rate(sum(reached(r, "screen") for r in members), n),
                    "interview": rate(sum(reached(r, "technical") for r in members), n),
                    "offer": rate(sum(reached(r, "offer") for r in members), n)})
    return out


def calibration(data: list[dict]) -> dict:
    """Does a higher fit score go with more responses and screens? Bands and
    verdicts side by side; monotone only if every reported band agrees."""
    bands = {g["group"]: g for g in by(data, "fit_band")}
    order = [b for b in ("0-49", "50-79", "80-100") if b in bands and bands[b]["response"]["rate"] is not None]
    rates = [bands[b]["response"]["rate"] for b in order]
    verdict = ("not enough resolved applications per band to say" if len(order) < 2 else
               "higher bands responded more often" if rates == sorted(rates) and rates[0] < rates[-1] else
               "higher bands did not respond more often")
    return {"by_band": list(bands.values()), "by_verdict": by(data, "fit_verdict"), "by_channel": by(data, "channel"),
            "by_role_family": by(data, "role_family"), "by_seniority": by(data, "seniority"),
            "reading": verdict, "min_n": MIN_N,
            "policies": sorted({snap.FIT_POLICY}), "backfilled": sum(1 for r in data if r["backfilled"])}


def bottleneck(data: list[dict]) -> dict:
    """The weakest measured step of the funnel, and the endings by how they
    are known. A reason is the employer's only when it is `confirmed`."""
    steps = [s for s in funnel(data)["conversion"] if s["rate"] is not None]
    weakest = min(steps, key=lambda s: s["rate"]) if steps else None
    reasons: dict[str, Counter] = defaultdict(Counter)
    for r in data:
        for e in r["events"]:
            if e["event"] in ("rejected", "withdrawn", "no_response"):
                reasons[e.get("basis") or "unknown"][e.get("reason") or "unknown"] += 1
    return {"weakest_step": weakest, "measured_steps": steps,
            "reasons": {basis: dict(c) for basis, c in reasons.items()},
            "note": ("Confirmed reasons are the employer's; impressions are yours; everything else is "
                     "a pattern in the counts, not a cause.")}


def missing_skills(data: list[dict], top: int = 8) -> list[dict]:
    """Requirements most often missing or partly met among resolved
    applications that got no response, against those that did."""
    words = lambda text: " ".join(text.lower().split()[:8])
    quiet = Counter(words(s) for r in data if resolved(r) and not reached(r, "response") for s in r["skills_missing"])
    heard = Counter(words(s) for r in data if resolved(r) and reached(r, "response") for s in r["skills_missing"])
    n_quiet = sum(1 for r in data if resolved(r) and not reached(r, "response"))
    n_heard = sum(1 for r in data if resolved(r) and reached(r, "response"))
    return [{"requirement": s, "in_no_response": k, "of_no_response": n_quiet,
             "in_response": heard.get(s, 0), "of_response": n_heard}
            for s, k in quiet.most_common(top) if k >= 3]


def recommendations(data: list[dict]) -> list[dict]:
    """Only where two groups both clear MIN_N and their 95% intervals do not
    overlap; each with its counts. Otherwise, how far the data is from saying."""
    out = []
    for dimension in DIMENSIONS:
        groups = [g for g in by(data, dimension) if g["response"]["rate"] is not None]
        for a in groups:
            for b in groups:
                ra, rb = a["response"], b["response"]
                if ra["rate"] > rb["rate"] and ra["ci95"][0] > rb["ci95"][1]:
                    out.append({"dimension": dimension, "better": a["group"], "worse": b["group"],
                                "text": f"{dimension.replace('_', ' ')} '{a['group']}' got responses on "
                                        f"{ra['k']} of {ra['n']} resolved applications against {rb['k']} of "
                                        f"{rb['n']} for '{b['group']}' (95% intervals {ra['ci95']} vs "
                                        f"{rb['ci95']}). An association in your own history, not a cause."})
    if not out:
        n = sum(1 for r in data if resolved(r))
        out.append({"dimension": "", "text": (
            f"No grounded recommendation yet: {n} resolved application(s). A comparison needs two groups of "
            f"at least {MIN_N} each whose 95% intervals do not overlap. Record responses, screens and "
            "rejections on each job's page to get there.")})
    return out


def report(today: Optional[str] = None, days: Optional[int] = None) -> dict:
    data = rows(today)
    return {"funnel": funnel(data, days), "calibration": calibration(data), "bottleneck": bottleneck(data),
            "missing_skills": missing_skills(data), "recommendations": recommendations(data),
            "min_n": MIN_N, "quiet_days": ev.QUIET_DAYS,
            "applications": [{k: r[k] for k in ("job_id", "company", "title", "stage", "outcome", "fit_score",
                                                "fit_band", "role_family", "channel", "applied_at")}
                             for r in data if r["applied"]]}
