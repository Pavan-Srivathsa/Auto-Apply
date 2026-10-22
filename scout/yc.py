"""Y Combinator's job board (ycombinator.com/jobs) as a Scout source.

Each role page carries its postings as JSON in the page (`data-page`):
title, company, location, salary, minimum experience and YC's own visa
line ("US citizen/visa only", "Will sponsor"). Applying is different from
every other source: it happens on Work at a Startup, behind the person's
YC login, so the fill cannot do it. A YC watch therefore lists and screens
roles but queues nothing by itself (`hold`); adding one tailors the resume
and letter, and the person applies on YC by hand.
"""
from __future__ import annotations

import html
import json
import re

import httpx

from scout import Posting, Watch
from scout.filter import YEARS_MIN

BASE = "https://www.ycombinator.com"
ROLES = ["software-engineer"]
DATA = re.compile(r'data-page="([^"]+)"')
YEARS = re.compile(r"(\d+)\+?\s*years?", re.I)


def _posted(created: str) -> str:
    """YC says "10 days" or "4 months"; an ISO date passes through."""
    from scout import lennys
    return created[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", created) else lennys.posted_date(created)


def parse(page: str) -> list[Posting]:
    match = DATA.search(page)
    if not match:
        raise RuntimeError("the YC jobs page carries no job data; the page may have changed")
    data = json.loads(html.unescape(match.group(1)))
    rows = []
    for job in data.get("props", {}).get("jobPostings", []):
        years = YEARS.search(str(job.get("minExperience") or ""))
        if years and int(years.group(1)) >= YEARS_MIN:
            continue                        # "6+ years" is not entry level, and costs a screen
        url = BASE + str(job.get("url") or "")
        # No `text`: the screen fetches the public job page and reads all of it.
        rows.append(Posting(id=str(job.get("id") or url), title=str(job.get("title") or ""), url=url,
                            location=str(job.get("location") or ""), company=str(job.get("companyName") or ""),
                            posted=_posted(str(job.get("createdAt") or ""))))
    return rows


def fetch(watch: Watch, http: httpx.Client) -> list[Posting]:
    seen: set[str] = set()
    rows: list[Posting] = []
    for role in (watch.args or {}).get("roles") or ROLES:
        page = http.get(f"{BASE}/jobs/role/{role}").raise_for_status().text
        for posting in parse(page):
            if posting.id not in seen:
                seen.add(posting.id)
                rows.append(posting)
    return rows
