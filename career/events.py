"""The outcome ledger: one append-only JSON line per event, in the data folder.

The person records what happened (a recruiter wrote, a screen, a rejection
and the reason the employer gave). The app adds only what it has evidence
for, and says how it knows: `applied` from the job's own status history
(the person's "I submitted it", or a confirmation page the watcher saw),
`response` from a reply the reply watcher read. Silence is never a
rejection: an application quiet for `QUIET_DAYS` is reported as "no
response yet", as the inference it is, and never written here.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import paths

# The funnel, in order. A job's stage is the furthest one any event reached.
STAGES = ("discovered", "shortlisted", "prepared", "applied", "response", "screen",
          "technical", "final", "offer")
RANK = {name: n for n, name in enumerate(STAGES)}
# Where a search can end. `no_response` is the person saying so, never the app.
OUTCOMES = ("rejected", "withdrawn", "no_response", "offer_accepted", "offer_declined", "unknown")
EVENTS = STAGES + OUTCOMES
# Why an application ended, when it did. `basis` says how it is known.
REASONS = ("no_recruiter_response", "recruiter_screen", "coding_interview", "ml_technical",
           "behavioral_or_system_design", "location_or_authorization", "freeze_or_closed", "unknown")
BASES = ("confirmed", "impression", "unknown")      # the employer said so / the person's impression / not known
QUIET_DAYS = 30


@dataclass
class Event:
    job_id: str
    event: str
    at: str                                  # ISO date (or datetime)
    source: str = "user"                     # user | inferred
    how: str = ""                            # for an inferred event: the evidence
    note: str = ""
    reason: str = ""                         # for rejected / withdrawn
    basis: str = ""                          # confirmed | impression | unknown
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    recorded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))


class EventError(ValueError):
    pass


_lock = threading.Lock()


def path() -> Path:
    return Path(os.environ.get("AUTOPILOT_OUTCOMES") or paths.DATA / "career" / "outcomes.jsonl")


def validate(event: Event) -> Event:
    if event.event not in EVENTS:
        raise EventError(f"event must be one of {EVENTS}")
    if event.source not in ("user", "inferred"):
        raise EventError("source must be user or inferred")
    if event.source == "inferred" and not event.how:
        raise EventError("an inferred event must say how it is known")
    if event.reason and event.reason not in REASONS:
        raise EventError(f"reason must be one of {REASONS}")
    if event.basis and event.basis not in BASES:
        raise EventError(f"basis must be one of {BASES}")
    if event.reason and event.event not in ("rejected", "withdrawn", "no_response"):
        raise EventError("a reason belongs on rejected, withdrawn or no_response")
    if event.reason and not event.basis:
        event.basis = "unknown"
    try:
        datetime.fromisoformat(event.at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EventError(f"at must be an ISO date: {event.at!r}") from exc
    return event


def record(event: Event) -> Event:
    """Append one event. Never edits or removes an earlier line."""
    validate(event)
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with _lock, open(target, "a") as handle:
        handle.write(json.dumps(asdict(event)) + "\n")
    return event


def recorded(job_id: Optional[str] = None) -> list[Event]:
    """The ledger, oldest first. A torn line is skipped, not fatal."""
    try:
        lines = path().read_text().splitlines()
    except OSError:
        return []
    out = []
    fields = set(Event.__dataclass_fields__)
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and (job_id is None or row.get("job_id") == job_id):
            out.append(Event(**{k: v for k, v in row.items() if k in fields}))
    return out


def _date(value: str) -> str:
    return (value or "")[:10]


def inferred(job, app_dir: Optional[Path], replies: Optional[list] = None) -> list[Event]:
    """What the app's own records show, recomputed on every read so it is
    never stale and never stored: discovered, prepared, applied, response."""
    out = [Event(job.id, "discovered", _date(job.added_at), "inferred", "the job was added to autopilot")]
    history = []
    if app_dir and (Path(app_dir) / "status.json").exists():
        try:
            history = json.loads((Path(app_dir) / "status.json").read_text()).get("history", [])
        except (OSError, ValueError):
            history = []
    # Older folders are re-tailors; the job's own status is the record that counts.
    for row in history:
        if row.get("status") == "awaiting_review":
            out.append(Event(job.id, "prepared", _date(row["at"]), "inferred", "documents tailored"))
            break
    submitted = [row for row in history if row.get("status") == "submitted"]
    if submitted or str(getattr(job.status, "value", job.status)) == "submitted":
        row = submitted[0] if submitted else {"at": job.added_at, "note": ""}
        note = row.get("note") or ""
        how = ("you marked it submitted" if "hand" in note or "submitted it" in note or not note
               else f"the app saw a confirmation ({note[:80]})")
        out.append(Event(job.id, "applied", _date(row["at"]), "inferred", how))
    for reply in replies or []:
        out.append(Event(job.id, "response", _date(reply.received_at), "inferred",
                         f"a reply the watcher read ({reply.verdict or 'unclassified'})"))
        break
    return out


def timeline(job, app_dir: Optional[Path], replies: Optional[list] = None) -> list[Event]:
    """Inferred and recorded events together, by date. A person's event of
    the same kind and day replaces the inference."""
    mine = recorded(job.id)
    have = {(e.event, _date(e.at)) for e in mine}
    rows = [e for e in inferred(job, app_dir, replies) if (e.event, _date(e.at)) not in have] + mine
    return sorted(rows, key=lambda e: (_date(e.at), RANK.get(e.event, 99)))


def stage_reached(events: list[Event]) -> str:
    reached = max((RANK[e.event] for e in events if e.event in RANK), default=0)
    return STAGES[reached]


def outcome(events: list[Event], today: Optional[str] = None) -> dict:
    """Where the application stands: the last recorded ending, else
    in flight or quiet. `quiet` is an inference about silence, reported as
    such, and never counted as a rejection."""
    endings = [e for e in events if e.event in OUTCOMES]
    if endings:
        last = endings[-1]
        return {"state": last.event, "source": last.source, "reason": last.reason, "basis": last.basis}
    applied = [e for e in events if e.event == "applied"]
    if not applied:
        return {"state": "not_applied", "source": "", "reason": "", "basis": ""}
    if RANK[stage_reached(events)] > RANK["applied"]:
        return {"state": "in_progress", "source": "", "reason": "", "basis": ""}
    now = datetime.fromisoformat(today) if today else datetime.now()
    age = (now - datetime.fromisoformat(_date(applied[0].at))).days
    if age >= QUIET_DAYS:
        return {"state": "quiet", "source": "inferred", "reason": "", "basis": "",
                "how": f"no response recorded in {age} days (not a rejection)"}
    return {"state": "in_flight", "source": "", "reason": "", "basis": "", "days": age}
