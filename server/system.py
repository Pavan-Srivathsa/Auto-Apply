"""What the app has been doing, for the System view: Scout coverage and
failures, fits, the bullet reviewer's verdicts and escalations, model usage
and cost, enrichment use and mail delivery. Counts only: no key, no
candidate text."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/metrics")
def metrics(days: int = 30) -> dict:
    from outreach import store as outreach_store
    from scout import store as scout_store
    from server import queue
    from tailor import costs
    watches = scout_store.watches()
    listed = [row for w in watches for row in w.listed]
    hits = scout_store.hits()
    reviews = {"files": 0, "PASS": 0, "WARN": 0, "FAIL": 0, "UNKNOWN": 0, "escalated": 0, "reverted": 0, "needs_human": 0}
    for job in queue.all_jobs():
        path = Path(job.app_dir or "") / "bullet_review.json"
        if not job.app_dir or not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        reviews["files"] += 1
        for b in data.get("bullets", []):
            reviews[b.get("verdict", "UNKNOWN")] = reviews.get(b.get("verdict", "UNKNOWN"), 0) + 1
            reviews["escalated"] += bool(b.get("escalated"))
        reviews["reverted"] += len(data.get("reverted", []))
        reviews["needs_human"] += bool(data.get("needs_human"))
    contacts = [c for o in outreach_store.all_jobs() for c in o.contacts]
    drafts = [d for o in outreach_store.all_jobs() for d in o.drafts]
    return {
        "scout": {"watches": len(watches), "curated": sum(1 for w in watches if w.args.get("curated")),
                  "checked": sum(1 for w in watches if w.last_checked), "broken": sum(1 for w in watches if w.error),
                  "errors": [{"company": w.company, "error": (w.error or "")[:120]} for w in watches if w.error][:20],
                  "roles_open": sum(w.last_total for w in watches), "roles_at_level": len(listed),
                  "roles_scored": sum(1 for r in listed if (r.get("fit") or {}).get("verdict")),
                  "hits": len(hits), "hits_mailed": sum(1 for h in hits if h.mailed)},
        "reviewer": reviews,
        "usage": costs.usage(days),
        "outreach": {"contacts": len(contacts), "apollo_enriched": sum(1 for c in contacts if c.source == "apollo" and c.email),
                     "drafts": len(drafts), "sent": sum(1 for d in drafts if d.status == "sent"),
                     "failed": sum(1 for d in drafts if d.status == "failed")},
    }
