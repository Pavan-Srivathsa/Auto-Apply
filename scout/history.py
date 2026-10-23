"""Listing history: whether a role is new, still open, changed, reposted,
back, possibly stale, closed, or not verifiable, with the evidence.

Rules, each one a lesson the detector Career-Ops ships learned on real data
(detect-reposts.mjs, liveness-core.mjs; github.com/career-ops-hq/career-ops,
MIT; reimplemented here):

- **Closing needs reconciliation.** A role absent from a *complete* listing
  is missing; it is CLOSED only after `MISSING_TO_CLOSE` consecutive
  successful checks without it. A failed fetch closes nothing. A provider
  that lists only a search slice (Lenny's, a16z, YC) never closes a role: a
  role it stops showing is UNVERIFIED.
- **A repost needs time.** A new identifier whose title has the same *set*
  of words as a role that closed, appearing at least `MIN_SPAN_DAYS` later
  and within `REPOST_WINDOW_DAYS`, is REPOSTED. Two listings of one title
  open at once are parallel openings (new headcount, another city), never a
  repost. "Commercial Engineer - Berlin" and "- Munich" are different titles.
- **The same job at a new URL** is a new identifier appearing in the same
  check its title twin went missing, with the same description fingerprint
  when there is one.
- **Age alone is not staleness.** POSSIBLY_STALE needs a long-open role
  *and* a repost or a return in its history. Nothing here calls a job fake,
  and nothing discards a role: freshness is shown next to the fit, not
  instead of it.
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import paths

NEW, ACTIVE, UPDATED, REPOSTED, RETURNED, STALE, CLOSED, UNVERIFIED = (
    "NEW", "ACTIVE", "UPDATED", "REPOSTED", "RETURNED", "POSSIBLY_STALE", "CLOSED", "UNVERIFIED")
MISSING_TO_CLOSE = 2
MIN_SPAN_DAYS = 1
REPOST_WINDOW_DAYS = 90
LONG_OPEN_DAYS = 60
FRESH_DAYS = 7
PARTIAL = {"lennys", "a16z", "yc"}       # providers that list a slice, not the whole board
KEEP_TRANSITIONS = 20
_lock = threading.Lock()


def _path() -> Path:
    return Path(os.environ.get("AUTOPILOT_SCOUT_HISTORY") or paths.DATA / "scout_history.json")


def _load() -> dict:
    try:
        return json.loads(_path().read_text())
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    target = _path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(target)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _days(a: str, b: str) -> float:
    return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 86400


def title_key(title: str) -> str:
    """The title's identity: its set of words, order and punctuation aside."""
    return " ".join(sorted(set(re.findall(r"[a-z0-9+#]+", (title or "").lower()))))


def _fingerprint(text: str) -> int:
    if not text or len(text) < 200:
        return 0
    from tailor import quality
    return quality.simhash(text)


def _move(record: dict, state: str, why: str, at: str) -> None:
    if record.get("state") != state:
        record.setdefault("transitions", []).append({"at": at, "from": record.get("state"), "to": state, "why": why})
        record["transitions"] = record["transitions"][-KEEP_TRANSITIONS:]
        record["state"] = state


def reconcile(watch, rows: list, at: Optional[str] = None) -> dict:
    """One successful check of a watch: every listed role and every record
    the listing no longer has. Returns {posting_id: record} for the rows."""
    from tailor import quality
    at = at or now()
    complete = watch.provider not in PARTIAL
    with _lock:
        data = _load()
        book = data.setdefault(watch.id, {})
        listed_ids = {p.id for p in rows}
        # Missing first, so a title twin that went missing in this very check is known.
        went_missing = []
        for pid, record in book.items():
            if pid in listed_ids or record["state"] == CLOSED:
                continue
            record["missing_checks"] = record.get("missing_checks", 0) + 1
            if not complete:
                _move(record, UNVERIFIED, "no longer in this provider's partial listing; cannot confirm closed", at)
            elif record["missing_checks"] >= MISSING_TO_CLOSE:
                record["removed_at"] = record.get("removed_at") or at
                _move(record, CLOSED, f"absent from {record['missing_checks']} complete listings in a row", at)
            went_missing.append(record)
        out = {}
        for posting in rows:
            key = title_key(posting.title)
            fp = _fingerprint(getattr(posting, "text", "") or "")
            record = book.get(posting.id)
            if record is None:
                record = {"id": posting.id, "url": posting.url, "title": posting.title, "title_key": key,
                          "company": getattr(posting, "company", "") or watch.company,
                          "location": posting.location, "first_seen": at, "fingerprint": fp,
                          "repost_count": 0, "missing_checks": 0, "failures": 0, "state": None, "transitions": []}
                twins = [r for r in book.values() if r["title_key"] == key and r["id"] != posting.id]
                moved = [r for r in went_missing if r["title_key"] == key and (not fp or not r.get("fingerprint")
                         or quality.distance(fp, r["fingerprint"]) <= 6)]
                taken = {r.get("repost_of") for r in book.values()}
                closed = [r for r in twins if r["state"] == CLOSED and r.get("removed_at") and r["id"] not in taken
                          and MIN_SPAN_DAYS <= _days(r["first_seen"], at) <= REPOST_WINDOW_DAYS]
                live = [r for r in twins if r["state"] not in (CLOSED,) and r["id"] in listed_ids]
                if live and not moved:
                    _move(record, NEW, "first seen (a parallel opening: another listing with this title is open)", at)
                elif moved:
                    prior = moved[0]
                    record |= {"repost_of": prior["id"], "repost_count": prior.get("repost_count", 0) + 1}
                    _move(record, REPOSTED, "same title and description at a new identifier, the old one gone "
                                            "in the same check", at)
                elif closed:
                    prior = max(closed, key=lambda r: r["removed_at"])
                    record |= {"repost_of": prior["id"], "repost_count": prior.get("repost_count", 0) + 1}
                    _move(record, REPOSTED, f"same title as a role closed {prior['removed_at'][:10]}, "
                                            f"first seen {prior['first_seen'][:10]}", at)
                else:
                    _move(record, NEW, "first seen", at)
                book[posting.id] = record
            else:
                was = record["state"]
                record["missing_checks"] = 0
                if was in (CLOSED, UNVERIFIED) and record.get("removed_at"):
                    record["reappeared_at"] = at
                    _move(record, RETURNED, f"listed again after closing {record['removed_at'][:10]}", at)
                    record["removed_at"] = None
                elif fp and record.get("fingerprint") and quality.distance(fp, record["fingerprint"]) > 6:
                    _move(record, UPDATED, "the description changed", at)
                elif was in (NEW, UNVERIFIED, UPDATED, REPOSTED, RETURNED, None):
                    _move(record, ACTIVE, "still listed", at)
                long_open = _days(record["first_seen"], at) >= LONG_OPEN_DAYS
                churn = record.get("repost_count", 0) > 0 or any(t["to"] == RETURNED for t in record["transitions"])
                if long_open and churn and record["state"] == ACTIVE:
                    _move(record, STALE, f"open {int(_days(record['first_seen'], at))} days and reposted or "
                                         "returned before", at)
                if fp:
                    record["fingerprint"] = fp
            record["last_seen"] = record["last_verified"] = at
            record["url"], record["title"] = posting.url, posting.title
            out[posting.id] = record
        _save(data)
    return out


def failure(watch, reason: str, at: Optional[str] = None) -> None:
    """A check that could not read the board: counted on every open record,
    and nothing is closed."""
    at = at or now()
    with _lock:
        data = _load()
        for record in data.get(watch.id, {}).values():
            if record["state"] != CLOSED:
                record["failures"] = record.get("failures", 0) + 1
                record["last_failure"] = {"at": at, "reason": reason[:200]}
        _save(data)


def freshness(record: Optional[dict], at: Optional[str] = None) -> dict:
    """A label for the page, with the evidence behind it."""
    if not record:
        return {"label": "verification_required", "evidence": ["no history yet"]}
    at = at or now()
    age = _days(record["first_seen"], at)
    evidence = [f"first seen {record['first_seen'][:10]} ({int(age)} days)",
                f"last verified {record.get('last_verified', '')[:10]}"]
    if record.get("repost_count"):
        evidence.append(f"reposted {record['repost_count']}× (previous id {record.get('repost_of')})")
    if record.get("failures"):
        evidence.append(f"{record['failures']} failed checks of its board")
    state = record["state"]
    label = ("verification_required" if state in (UNVERIFIED, CLOSED) or record.get("failures", 0) >= 3 else
             "reposted" if state == REPOSTED or (record.get("repost_count") and age < FRESH_DAYS) else
             "potentially_stale" if state == STALE else
             "recently_updated" if state == UPDATED else
             "fresh" if age <= FRESH_DAYS else "open")
    return {"label": label, "state": state, "evidence": evidence,
            "transitions": record.get("transitions", [])[-5:]}


def records(watch_id: Optional[str] = None) -> list[dict]:
    data = _load()
    items = [(watch_id, data.get(watch_id, {}))] if watch_id else list(data.items())
    return [r | {"watch_id": w} for w, book in items for r in book.values()]


def summary() -> dict:
    counts: dict[str, int] = {}
    for r in records():
        label = freshness(r)["label"]
        counts[label] = counts.get(label, 0) + 1
    return counts
