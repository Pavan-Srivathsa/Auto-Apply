"""What the app has been doing, for the System view: Scout coverage and
failures, fits, the bullet reviewer's verdicts and escalations, model usage
and cost, enrichment use and mail delivery. Counts only: no key, no
candidate text."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(prefix="/system", tags=["system"])


CREDITS_URL = "https://openrouter.ai/api/v1/credits"
KEY_URL = "https://openrouter.ai/api/v1/key"
BALANCE_TTL = 300                     # seconds between balance reads
_balance: dict = {"at": 0.0, "value": None}


def _read_balance() -> dict:
    """OpenRouter's own account numbers. Both endpoints are account reads and
    cost no credits; read at most every BALANCE_TTL seconds anyway."""
    import os

    import httpx
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    manage = os.environ.get("OPENROUTER_MANAGEMENT_KEY", "").strip()
    if not key:
        return {"ok": False, "error": "no key"}
    try:
        # The key the models run on: a management (provisioning) key reads
        # the account but every model call is refused with "User not found".
        r = httpx.get(KEY_URL, headers={"Authorization": f"Bearer {key}"}, timeout=10)
        if r.status_code != 200:
            msg = _message(r)
            return {"ok": False, "error": "key expired" if "expired" in msg.lower() else "key refused"}
        d = r.json().get("data", {})
        if d.get("is_provisioning_key"):
            return {"ok": False, "error": "management key: models refuse it"}
        out = {"ok": True, "left": None if d.get("limit_remaining") is None else round(float(d["limit_remaining"]), 4),
               "used": round(float(d.get("usage") or 0), 4), "total": d.get("limit")}
        # The account's balance, when a key that may read it is around.
        for k in (manage, key):
            if not k:
                continue
            r = httpx.get(CREDITS_URL, headers={"Authorization": f"Bearer {k}"}, timeout=10)
            if r.status_code == 200:
                c = r.json().get("data", {})
                total, used = float(c.get("total_credits") or 0), float(c.get("total_usage") or 0)
                return {"ok": True, "left": round(total - used, 4), "used": round(used, 4), "total": round(total, 4)}
        return out
    except (httpx.HTTPError, ValueError) as exc:
        return {"ok": False, "error": type(exc).__name__}


def _message(r) -> str:
    try:
        return (r.json().get("error") or {}).get("message", "")
    except ValueError:
        return ""


@router.get("/credits")
def credits(fresh: bool = False) -> dict:
    """The header's counter: what was spent today and in the last 7 days
    (Autopilot's own ledger, no network), and the balance left on OpenRouter."""
    import time

    from tailor import costs
    if fresh or time.time() - _balance["at"] > BALANCE_TTL or _balance["value"] is None:
        _balance["value"], _balance["at"] = _read_balance(), time.time()
    week = costs.usage(7)
    from datetime import date
    today = week["by_day"].get(date.today().isoformat(), 0.0)
    return {"today": today, "week": week["usd"],
            "by_provider": {k: v["usd"] for k, v in week["by_provider"].items()},
            "openrouter": _balance["value"], "checked_at": _balance["at"]}


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
