"""a16z's portfolio job board (jobs.a16z.com) as a Scout source.

The board renders its cards on the server, newest first, about 25 to a
search, and every card's Apply link is the employer's own form (Greenhouse,
Ashby, Lever, a careers page), so a role found here is queued like any
other and filled by its host. There is no paging a script can reach, so a
watch runs a few searches (`args.queries`) and unites them; checked
several times a day, the newest 25 of each is what is new.
"""
from __future__ import annotations

import html
import re
from urllib.parse import quote

import httpx

from scout import Posting, Watch
from scout import lennys

URL = "https://jobs.a16z.com/jobs"
QUERIES = ["software engineer", "machine learning", "ai engineer", "data scientist", "new grad"]
APPLY = re.compile(r'<a href="(https?://[^"]+)" target="_blank" rel="noopener noreferrer" '
                   r'class="relative z-10 mt-2\.5')
TAG = re.compile(r"<[^>]+>")
SALARY = re.compile(r"^(?:USD|EUR|GBP|CAD|INR|\$|€|£)|\d[\d,.]*\s*[–-]\s*\d", re.I)
AGE = re.compile(r"^Posted\s+(.*?)(?:\s+ago)?$", re.I)


def _age(text: str) -> str:
    """"Posted an hour ago" / "Posted 10 days ago" as an ISO date."""
    match = AGE.match(text.strip())
    if not match:
        return ""
    when = re.sub(r"^(?:an?|one)\s+", "1 ", match.group(1).strip(), flags=re.I)
    return lennys.posted_date(when if re.match(r"^\d", when) else "today")


def parse(page: str) -> list[Posting]:
    """Every card on one result page. A card is everything between the
    previous card's Apply link and its own; its text runs company, title,
    an optional salary, location, "Posted ...", then the company's facts."""
    rows: list[Posting] = []
    previous = 0
    for match in APPLY.finditer(page):
        tokens = [html.unescape(t).strip() for t in TAG.split(page[previous:match.start()])]
        tokens = [t for t in tokens if t and t != "·" and not t.endswith('">')]
        previous = match.end()
        at = next((i for i in range(len(tokens) - 1, -1, -1) if AGE.match(tokens[i])), None)
        if at is None or at < 3:
            continue
        location = tokens[at - 1]
        j = at - 2
        if SALARY.search(tokens[j]):
            j -= 1
        title, company = tokens[j], tokens[j - 1]
        url = lennys.clean_url(html.unescape(match.group(1)))
        rows.append(Posting(id=url, title=title, url=url, location=location,
                            posted=_age(tokens[at]), company=company))
    return rows


def fetch(watch: Watch, http: httpx.Client) -> list[Posting]:
    """Every role the watch's searches return, each link once, newest first."""
    seen: set[str] = set()
    rows: list[Posting] = []
    for query in (watch.args or {}).get("queries") or QUERIES:
        page = http.get(f"{URL}?q={quote(query)}").raise_for_status().text
        for posting in parse(page):
            if posting.id not in seen:
                seen.add(posting.id)
                rows.append(posting)
    if not rows:
        raise RuntimeError("jobs.a16z.com showed no job cards; the page layout may have changed")
    rows.sort(key=lambda p: p.posted, reverse=True)
    return rows
