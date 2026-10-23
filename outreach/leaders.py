"""Who to write to about one job: the people who run the engineering
function the role sits in, ranked, with the reason and the evidence.

Free people search only (`apollo.search`, which spends no enrichment
credits); an email address comes from the paid enrichment behind its own
button, never from a guess. Ranking follows the person's order (hiring
manager, engineering manager, director, head of AI/ML, VP Engineering, CTO,
technical co-founder, recruiter) and, within it, team alignment: for an
inference role an inference or platform leader comes before an unrelated
executive. Contacts already on file for the company come first, as warm
leads (the idea of Career-Ops' contact-lookup.mjs, MIT, reimplemented).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from career.snapshot import role_family

from . import apollo, store
from .models import Contact

# (tier, pattern) in the person's order; the first that matches a title wins.
TIERS = [
    (1, "hiring manager", r"hiring manager"),
    (2, "engineering manager", r"\b(engineering|software|ml|machine learning|ai|platform|infrastructure)\b.*\bmanager\b|\bmanager\b.*\bengineering\b"),
    (3, "engineering director", r"\bdirector\b"),
    (4, "head of AI/ML", r"\bhead of (ai|ml|machine learning|applied ai|research)|\b(vp|vice president)\b.*\b(ai|ml)\b"),
    (5, "VP Engineering", r"\b(vp|vice president)\b.*\bengineering\b|\bhead of engineering\b"),
    (6, "CTO", r"\bcto\b|chief technology officer"),
    (7, "technical co-founder", r"\bco-?founder\b|\bfounder\b|\bceo\b|chief executive"),
    (8, "recruiter", r"recruit|talent"),
]
# The words that put a leader on the same team as a role of this family.
TEAMS = {
    "inference": ["inference", "serving", "platform", "infrastructure", "ml systems", "performance", "runtime"],
    "agents": ["agent", "applied ai", "ai", "product engineering"],
    "evaluation": ["eval", "quality", "research", "applied ai"],
    "ml": ["machine learning", "ml", "ai", "data science"],
    "ai_engineer": ["ai", "applied ai", "ml", "llm", "product engineering"],
    "forward_deployed": ["forward deployed", "solutions", "customer engineering", "deployment"],
    "backend": ["backend", "platform", "infrastructure", "core"],
    "data_science": ["data science", "analytics", "data"],
    "research": ["research", "ml", "ai"],
}
PER_ROLE = 4


def tier(title: str) -> tuple[int, str]:
    low = (title or "").lower()
    for rank, label, pattern in TIERS:
        if re.search(pattern, low):
            return rank, label
    return 9, "other"


def alignment(title: str, family: str) -> list[str]:
    low = (title or "").lower()
    return [w for w in TEAMS.get(family, []) if re.search(rf"\b{re.escape(w)}\b", low)]


def rank(contacts: list[Contact], job_title: str, job_id: str = "") -> list[Contact]:
    """Each contact with its reason and confidence, best first."""
    family = role_family(job_title)
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    scored = []
    for c in contacts:
        level, label = tier(c.title)
        aligned = alignment(c.title, family)
        confidence = round(max(0.1, 1.0 - 0.08 * (level - 1)) * (1.0 if aligned or level == 8 else 0.7), 2)
        c.team_alignment = ", ".join(aligned)
        c.related_job = job_id
        c.confidence = confidence
        kind = family.replace("_", " ")
        article = "an" if kind[:1] in "aeiou" else "a"
        c.reason = (f"{label}" + (f", on the {', '.join(aligned)} side, the team {article} {kind} "
                                  f"role reports into" if aligned else
                                  "" if level == 8 else f"; no sign in the title of the {kind} team"))
        c.evidence = list(dict.fromkeys((c.evidence or []) + [f"{c.source} people search: '{c.title}' at {c.company}"]))
        c.last_verified = c.last_verified or at
        scored.append((level - (0.5 if aligned else 0), -confidence, c))
    return [c for _, _, c in sorted(scored, key=lambda row: (row[0], row[1]))]


def research(job_id: str, company: str, job_title: str, client=None) -> dict:
    """Search, rank and keep. Warm contacts already on file at the company
    come first. Never enriches, never guesses an email."""
    warm = [c for o in store.all_jobs() if o.job_id != job_id for c in o.contacts
            if c.company and company and c.company.lower() == company.lower()]
    found: list[Contact] = []
    errors = []
    if apollo.configured():
        for role in ("hiring_manager", "ai_leadership", "recruiter"):
            try:
                found.extend(apollo.search(company, role, PER_ROLE, client=client))
            except apollo.ApolloError as exc:
                errors.append(str(exc)[:160])
    else:
        errors.append("Apollo is not set up; add the key on this page to search")
    seen, unique = set(), []
    for c in found:
        key = c.apollo_id or c.linkedin_url or c.name.lower()
        if key not in seen:
            seen.add(key)
            unique.append(c)
    ranked = rank(unique, job_title, job_id)
    for c in warm:
        c.reason = "already in your contacts from another application at this company: " + (c.reason or c.title)
    if ranked:
        store.add_contacts(job_id, ranked, note=f"leadership research for {job_title}")
    return {"warm": [c.model_dump() for c in warm], "contacts": [c.model_dump() for c in ranked],
            "family": role_family(job_title), "errors": errors}
