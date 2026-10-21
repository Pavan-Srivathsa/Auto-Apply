"""The outreach watcher: replies in, follow-up drafts out, good news on Slack.

Every TICK seconds, while the server runs:

1. Every email we sent is matched against the inbox (`outreach.replies`,
   read-only). A new answer is judged (`tailor.replies`: Jev's verdict, the
   text model's one-line why), kept on the job's outreach state, and a
   positive one is posted to Slack in the person's format with its Win #.
2. A thread with no answer, whose last email went out FOLLOW_DAYS ago, gets
   the next follow-up written as a draft, at most MAX_FOLLOWUPS per person.
   It waits on the review page like any draft; the person sends it.

Nothing here sends an email or can: this module imports no mail sender,
and `tests/test_invariants.py` holds that. `AUTOPILOT_REPLIES=0` turns it off.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from outreach import drafting, replies, slack, store
from outreach.models import Draft, Outreach, Reply, now

log = logging.getLogger("autopilot.outreach")

ENV = "AUTOPILOT_REPLIES"
TICK = 600                 # seconds between looks at the inbox
FOLLOW_DAYS = 2            # quiet days before the next follow-up is drafted
MAX_FOLLOWUPS = 2

_thread: Optional[threading.Thread] = None
_stop = threading.Event()


def _sent(outreach: Outreach) -> list[Draft]:
    return [d for d in outreach.drafts if d.status == "sent" and d.message_id]


def label(draft: Draft) -> str:
    return "Follow-up " + str(draft.followup) if draft.followup else "First email"


def _when(iso: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(iso) if iso else None
    except ValueError:
        return None


# ------------------------------------------------------------- replies --
def check_replies(rows: list[Outreach], find=replies.find, classify=None) -> int:
    """Record new answers to our emails. Returns how many were new."""
    from server import queue
    from tailor import replies as judge
    classify = classify or judge.classify
    by_id = {d.message_id: (row.job_id, d) for row in rows for d in _sent(row)}
    if not by_id:
        return 0
    known = {r.id for row in rows for r in row.replies}
    oldest = min(_when(d.sent_at) or datetime.now(timezone.utc) for _, d in by_id.values())
    own = os.environ.get(replies.USER_ENV, "").strip().lower()
    new = 0
    for found in find(by_id, since=oldest - timedelta(days=1)):
        if found.message_id in known or replies.sender_address(found.sender) == own:
            continue
        job_id, draft = by_id[found.answers]
        job = queue.get(job_id)
        role = f"{job.title} at {job.company}" if job else ""
        verdict = classify(found.text, role)
        outreach = store.get(job_id)
        wins = sum(1 for row in store.all_jobs() for r in row.replies if r.verdict == "positive")
        reply = Reply(id=found.message_id, contact_id=draft.contact_id, draft_id=draft.id,
                      received_at=found.received_at, sender=found.sender, text=found.text,
                      verdict=verdict.verdict, why=verdict.why,
                      win=wins + 1 if verdict.verdict == "positive" else 0)
        outreach.replies.append(reply)
        store.save(outreach)
        known.add(found.message_id)
        new += 1
    return new


def announce(rows: list[Outreach], post=slack.post) -> int:
    """Post every positive reply not yet on Slack. Returns how many went."""
    from server import queue
    if not slack.configured() and post is slack.post:
        return 0
    posted = 0
    for row in rows:
        job = queue.get(row.job_id)
        changed = False
        for reply in row.replies:
            if reply.verdict != "positive" or reply.posted:
                continue
            contact = row.contact(reply.contact_id)
            draft = row.draft(reply.draft_id)
            if contact is None or draft is None:
                continue
            text = slack.message(
                candidate=os.environ.get(replies.USER_ENV, "").strip() or "you",
                name=contact.name, title=contact.title, company=contact.company or (job.company if job else ""),
                role=job.title if job else "", subject=draft.subject, replied_to=label(draft),
                sent_at=draft.sent_at or reply.received_at, received_at=reply.received_at,
                reply=reply.text, why=reply.why, win=reply.win)
            try:
                post(text)
            except slack.SlackError as exc:
                log.warning("slack: %s", exc)
                continue
            reply.posted = changed = True
            posted += 1
        if changed:
            store.save(row)
    return posted


# ----------------------------------------------------------- follow-ups --
def due_followups(row: Outreach, at: Optional[datetime] = None) -> list[tuple[Draft, int]]:
    """(last email, next follow-up number) for every thread that is due."""
    at = at or datetime.now(timezone.utc)
    answered = {r.contact_id for r in row.replies}
    due = []
    for contact_id in {d.contact_id for d in row.drafts}:
        if contact_id in answered:
            continue
        thread = sorted((d for d in row.drafts if d.contact_id == contact_id and d.status != "failed"),
                        key=lambda d: d.followup)
        if not thread or any(d.status != "sent" for d in thread):
            continue                        # nothing sent yet, or a draft already waits on the person
        last = thread[-1]
        sent_at = _when(last.sent_at)
        if last.followup >= MAX_FOLLOWUPS or sent_at is None or at - sent_at < timedelta(days=FOLLOW_DAYS):
            continue
        due.append((last, last.followup + 1))
    return due


def write_followups(rows: list[Outreach], at: Optional[datetime] = None) -> int:
    """Draft every due follow-up. Returns how many were written."""
    from server import queue
    from tailor import brief as briefs
    from tailor import outreach as writer
    written = 0
    for row in rows:
        due = due_followups(row, at)
        if not due:
            continue
        job = queue.get(row.job_id)
        if job is None or not job.app_dir or not Path(job.app_dir).exists():
            continue
        app_dir = Path(job.app_dir)
        for last, n in due:
            contact = row.contact(last.contact_id)
            if contact is None:
                continue
            thread = sorted((d for d in row.drafts if d.contact_id == contact.id and d.status == "sent"),
                            key=lambda d: d.followup)
            try:
                text = writer.write_followup(drafting.context(app_dir), contact, thread, n,
                                             drafting.candidate_name(app_dir), brief=briefs.load(app_dir))
            except Exception as exc:  # noqa: BLE001 - tried again on the next check
                log.warning("follow-up for %s: %s", contact.name, exc)
                continue
            store.put_draft(row.job_id, Draft.create(
                contact.id, text.subject, text.body, attach_resume=False,
                resume_folder=last.resume_folder, model=text.model, followup=n, reply_to=last.id))
            written += 1
    return written


# ---------------------------------------------------------------- loop --
def tick() -> dict:
    rows = store.all_jobs()
    result = {"replies": 0, "posted": 0, "followups": 0}
    if replies.configured():
        try:
            result["replies"] = check_replies(rows)
        except replies.ReplyError as exc:
            log.warning("replies: %s", exc)
        rows = store.all_jobs()
    result["posted"] = announce(rows)
    result["followups"] = write_followups(store.all_jobs())
    return result


def _loop() -> None:
    while not _stop.is_set():
        started = time.monotonic()
        try:
            tick()
        except Exception:  # noqa: BLE001 - the watcher outlives any one failure
            log.exception("outreach watch failed")
        _stop.wait(max(1.0, TICK - (time.monotonic() - started)))


def start() -> bool:
    global _thread
    if os.environ.get(ENV, "1") == "0" or (_thread and _thread.is_alive()):
        return False
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="autopilot-outreach", daemon=True)
    _thread.start()
    return True


def stop() -> None:
    _stop.set()
