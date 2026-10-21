"""What a draft is written from: the candidate's name and the job's context.

Kept apart from `server/outreach.py`, which holds the send route, so code
that runs on its own (the reply watcher writing follow-up drafts) can write
a draft without importing anything that sends.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import paths
from outreach import apollo, store
from outreach.models import Contact, Draft
from tailor import brief as briefs
from tailor import outreach as writer
from tailor import profile, tailor
from tailor.answers import Context

AUTO_ROLES = ("recruiter", "hiring_manager")     # one of each: credits are spent per address


class DraftError(RuntimeError):
    """Why a draft cannot be written; `status` is the HTTP code the route answers."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail


class MissingName(DraftError, LookupError):
    def __init__(self, detail: str) -> None:
        super().__init__(409, detail)


def candidate_name(app_dir: Path) -> str:
    form = paths.BASE / "form.json"
    try:
        data = json.loads(form.read_text()) if form.exists() else {}
    except (OSError, ValueError):
        data = {}
    if isinstance(data, dict):
        full = str(data.get("full_name") or "").strip()
        if full:
            return full
        name = " ".join(str(data.get(key) or "").strip() for key in ("first_name", "last_name")).strip()
        if name:
            return name
    letter = app_dir / "cover_letter.md"
    if letter.exists():
        match = re.search(r"(?im)^(?:Sincerely|Best),\s*\n([^\n]+)", letter.read_text())
        if match:
            return match.group(1).strip()
    raise MissingName("your name is missing from Form details and the cover letter")


def context(app_dir: Path) -> Context:
    applicant = paths.BASE / "applicant.md"
    return Context.from_app_dir(
        app_dir,
        profile=tailor.load_profile(stories=profile.read_used(app_dir)),
        applicant=applicant.read_text(errors="replace") if applicant.exists() else "",
    )


def job_and_dir(job_id: str):
    from server import queue
    job = queue.get(job_id)
    if job is None:
        raise DraftError(404, f"no job {job_id}")
    if not job.app_dir:
        raise DraftError(409, "the job has no tailored application folder yet")
    app_dir = Path(job.app_dir)
    if not app_dir.exists():
        raise DraftError(409, "the tailored application folder is missing")
    return job, app_dir


def ensure_email(job_id: str, contact: Contact) -> Contact:
    if contact.email:
        return contact
    if not (contact.linkedin_url or contact.apollo_id):
        raise DraftError(409, f"{contact.name} has no email yet")
    try:
        contact = apollo.enrich(contact)
    except apollo.ApolloError as exc:
        raise DraftError(502, str(exc)) from None
    store.add_contacts(job_id, [contact], f"Apollo returned {contact.email_status or 'an email'}.")
    return contact


def write_draft(job, app_dir: Path, contact: Contact, attach_resume: bool) -> Draft:
    if not contact.email:
        raise DraftError(409, "find or enter an email before drafting")
    try:
        return writer.draft(
            context(app_dir), contact, job.title, job.company,
            candidate_name(app_dir), attach_resume, app_dir.name,
            brief=briefs.load(app_dir),
        )
    except writer.OutreachError as exc:
        raise DraftError(502, str(exc)) from None


def prepare(job_id: str) -> str:
    """Find the company's people and write one draft per role, for a job
    that came in on its own (Lenny's Jobs). Never sends: a draft waits on
    the review page until the person approves it. Returns one line for the
    log; every failure is a line, not an exception, since the job's documents
    are already done and outreach is the extra."""
    if not apollo.configured():
        return "outreach skipped: no Apollo key"
    try:
        job, app_dir = job_and_dir(job_id)
    except DraftError as exc:
        return f"outreach skipped: {exc.detail}"
    if not job.company.strip():
        return "outreach skipped: the job has no company name"
    if store.get(job_id).drafts:
        return "outreach skipped: drafts already exist"
    try:
        found = apollo.discover(job.company, 2)
    except apollo.ApolloError as exc:
        return f"outreach skipped: {exc}"
    store.add_contacts(job_id, found, f"Apollo found {len(found)} contact(s) for you automatically.")
    written, notes, first = 0, [], None
    for role in AUTO_ROLES:
        for contact in (c for c in found if c.role == role):
            try:
                draft = write_draft(job, app_dir, ensure_email(job_id, contact), True)
            except DraftError as exc:          # no email for this one: try the next of the role
                notes.append(f"{contact.name}: {exc.detail}")
                continue
            store.put_draft(job_id, draft)
            first = first or draft.id
            written += 1
            break
    outreach = store.get(job_id)
    outreach.lookup_note = f"Wrote {written} email draft(s) automatically; none is sent until you approve it." + (
        " " + "; ".join(notes) if notes else "")
    if first:
        outreach.active_draft = first
    store.save(outreach)
    return f"outreach: {written} draft(s) for {job.company}, nothing sent"
