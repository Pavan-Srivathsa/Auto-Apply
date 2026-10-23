"""The application tracker: every job the person put into Autopilot, one row
each, with when the role was posted, when it was found, when they applied,
where it stands, and what the outreach did.

Nothing here is new data. The posting date and place come from Scout's own
listing of the role (matched by the board's link, else company and title)
or the archived posting; the applied date from the job's status history;
the stage and outcome from the outcome ledger (career/events.py: the
person's events, rejections confirmed from mail, replies the watcher read);
the emails, replies and follow-ups from the outreach store, which the reply
watcher keeps current. Read on demand, so it never drifts from those.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from . import events as ev


def _canonical(url: str) -> str:
    try:
        from identity import keys
        return keys.canonical(url)
    except Exception:  # noqa: BLE001
        return (url or "").split("?")[0].rstrip("/")


def _key(company: str, title: str) -> str:
    return re.sub(r"[^a-z0-9|]+", " ", f"{company}|{title}".lower()).strip()


def scout_index() -> tuple[dict, dict]:
    """Scout's rows by canonical link and by company|title."""
    from scout import store
    by_url, by_name = {}, {}
    for w in store.watches():
        for r in w.listed:
            row = {"posted": r.get("posted", ""), "location": r.get("location", ""), "source": w.company}
            by_url.setdefault(_canonical(r["url"]), row)
            by_name.setdefault(_key(r.get("company") or w.company, r["title"]), row)
    for h in store.hits():
        p = h.posting
        row = {"posted": p.get("posted", ""), "location": p.get("location", ""), "source": h.company}
        by_url.setdefault(_canonical(p.get("url", "")), row)
        by_name.setdefault(_key(h.company, p.get("title", "")), row)
    return by_url, by_name


def _posting_location(app_dir: Optional[Path]) -> str:
    if not app_dir:
        return ""
    try:
        head = (app_dir / "posting.md").read_text(errors="replace")[:1500]
    except OSError:
        return ""
    found = re.search(r"^\*\*Location:\*\*\s*(.+)$", head, re.M)
    if not found:
        return ""
    # Some boards leave a structured-data block in the place ("{'@type': 'Country', ...}").
    place = re.sub(r"\{[^}]*\}?", "", found.group(1))
    return re.sub(r"\s*,\s*(?=,|$)", "", re.sub(r"\s+", " ", place)).strip(" ,")


def _outreach(job_id: str) -> dict:
    try:
        from outreach import store, watch
        row = store.get(job_id)
    except Exception:  # noqa: BLE001 - no outreach for this job
        return {}
    sent = [d for d in row.drafts if d.status == "sent"]
    try:
        due = len(watch.due_followups(row))
    except Exception:  # noqa: BLE001
        due = 0
    return {"contacts": len(row.contacts), "sent": len(sent),
            "followups_sent": sum(1 for d in sent if d.followup),
            "last_sent": max((d.sent_at or "" for d in sent), default="")[:10],
            "replies": len(row.replies),
            "last_reply": max((r.received_at for r in row.replies), default="")[:10],
            "reply_verdict": (sorted(row.replies, key=lambda r: r.received_at)[-1].verdict if row.replies else ""),
            "followup_due": due}


def clean_title(title: str, company: str = "") -> str:
    """"Job Application for Data Scientist at Invisible" -> "Data Scientist"."""
    t = re.sub(r"^\s*job application for\s+", "", title or "", flags=re.I)
    if company:
        t = re.sub(rf"\s+(?:at|@|-|\|)\s+{re.escape(company)}.*$", "", t, flags=re.I)
    return re.sub(r"\s+at\s+[A-Z][\w&.,' -]{1,40}$", "", t).strip() or title


def in_autopilot() -> tuple[dict, dict]:
    """Jobs already in Autopilot, by canonical link and by company|title."""
    from server import queue
    by_url, by_name = {}, {}
    for job in queue.all_jobs():
        mark = {"id": job.id, "status": getattr(job.status, "value", str(job.status))}
        by_url[_canonical(job.url)] = mark
        by_name[_key(job.company, clean_title(job.title, job.company))] = mark
    return by_url, by_name


def rows() -> list[dict]:
    from server import queue
    by_url, by_name = scout_index()
    out = []
    for job in queue.all_jobs():
        app_dir = Path(job.app_dir) if job.app_dir else None
        try:
            from outreach import store as outreach_store
            replies = outreach_store.get(job.id).replies
        except Exception:  # noqa: BLE001
            replies = []
        timeline = ev.timeline(job, app_dir, replies)
        scout = (by_url.get(_canonical(job.url)) or by_name.get(_key(job.company, job.title))
                 or by_name.get(_key(job.company, clean_title(job.title, job.company))) or {})
        applied = next((e for e in timeline if e.event == "applied"), None)
        state = ev.outcome(timeline)
        last = timeline[-1] if timeline else None
        out.append({
            "id": job.id, "title": clean_title(job.title, job.company), "company": job.company, "url": job.url,
            "status": getattr(job.status, "value", str(job.status)),
            "location": _posting_location(app_dir) or scout.get("location", ""),
            "posted": scout.get("posted", ""), "found": (job.added_at or "")[:10],
            "applied": applied.at[:10] if applied else "", "applied_how": applied.how if applied else "",
            "stage": ev.stage_reached(timeline), "outcome": state["state"],
            "outcome_how": state.get("how", "") or (f"{state.get('reason')} ({state.get('basis')})" if state.get("reason") else ""),
            "last_event": {"event": last.event, "at": last.at[:10], "source": last.source} if last else None,
            "source": job.source or scout.get("source", ""),
            "outreach": _outreach(job.id),
        })
    out.sort(key=lambda r: (r["applied"] or "", r["found"]), reverse=True)
    return out
