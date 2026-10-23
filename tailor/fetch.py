"""Posting scraper.

Pulls a job page down over plain HTTP and reduces it to readable text. This
handles server-rendered postings, which covers Greenhouse, Lever, Ashby, and
most company career pages. Postings behind a JavaScript render or a login are
left to the browser layer, which already has a real Chrome profile; a caller
that gets `JavaScriptRequired` should fall back to it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import parse_qs, urlparse

import httpx

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
TIMEOUT = 30.0
MIN_USEFUL_CHARS = 400
SKIP_TAGS = {"script", "style", "noscript", "svg", "head"}
BLOCK_TAGS = {
    "p", "div", "section", "article", "br", "li", "ul", "ol", "tr",
    "h1", "h2", "h3", "h4", "h5", "h6",
}


class JavaScriptRequired(RuntimeError):
    """The page returned too little text to be a real posting."""


@dataclass
class Posting:
    url: str
    text: str
    title: str = ""
    company: str = ""
    location: str = ""
    # Which copy of the text this is: the fetched page, what the browser
    # saw, or Jobright's copy. Set by the pipeline when it picked.
    text_source: str = ""
    # Read from the board's own API rather than scraped off a page, so no
    # other copy of the posting competes with it (`tailor.boards`).
    authoritative: bool = False

    def to_markdown(self) -> str:
        header = [f"# {self.title or 'Job posting'}"]
        if self.company:
            header.append(f"**Company:** {self.company}")
        if self.location:
            header.append(f"**Location:** {self.location}")
        header.append(f"**Source:** {self.url}")
        if self.text_source:
            header.append(f"**Text from:** {self.text_source}")
        return "\n\n".join(header) + "\n\n---\n\n" + self.text + "\n"


class _TextExtractor(HTMLParser):
    """Flattens HTML to text, keeping block-level line breaks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0
        self.ld_json: list[str] = []
        self._in_ld = False

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            if dict(attrs).get("type") == "application/ld+json":
                self._in_ld = True
            self._skip_depth += 1
        elif tag in SKIP_TAGS:
            self._skip_depth += 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n- ")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS or tag == "script":
            self._skip_depth = max(0, self._skip_depth - 1)
            self._in_ld = False
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_ld:
            self.ld_json.append(data)
        elif self._skip_depth == 0 and data.strip():
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n\s*\n\s*\n+", "\n\n", raw)
        return "\n".join(line.strip() for line in raw.splitlines()).strip()


def _job_posting_ld(blobs: list[str]) -> Optional[dict]:
    """Find a schema.org JobPosting among the page's JSON-LD blocks."""
    for blob in blobs:
        try:
            parsed = json.loads(blob)
        except json.JSONDecodeError:
            continue
        candidates = parsed if isinstance(parsed, list) else [parsed]
        candidates += parsed.get("@graph", []) if isinstance(parsed, dict) else []
        for item in candidates:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return None


def _strip_html(value: str) -> str:
    extractor = _TextExtractor()
    extractor.feed(value)
    return extractor.text()


GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job}"


def greenhouse_job(url: str) -> Optional[tuple[str, str]]:
    """(board, job id) for a Greenhouse posting link, else None. The embedded
    form (`/embed/job_app?for=stripe&token=8224915`) shows only the
    application questions, and every Greenhouse job is also in the public
    board API with its full description (2026-10-22, Stripe)."""
    parts = urlparse(url)
    if not parts.netloc.endswith("greenhouse.io"):
        return None
    query = parse_qs(parts.query)
    board = (query.get("for") or [""])[0]
    job = (query.get("token") or query.get("gh_jid") or [""])[0]
    path = [p for p in parts.path.split("/") if p]
    if not board and len(path) >= 3 and path[1] == "jobs":
        board, job = path[0], job or path[2]
    return (board, job) if board and job.isdigit() else None


def _greenhouse(url: str, client: httpx.Client) -> Optional[Posting]:
    found = greenhouse_job(url)
    if not found:
        return None
    try:
        response = client.get(GREENHOUSE_API.format(board=found[0], job=found[1]))
        data = response.json() if response.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        return None
    text = _strip_html(unescape(str(data.get("content") or "")))
    if len(text) < MIN_USEFUL_CHARS:
        return None
    location = data.get("location") if isinstance(data.get("location"), dict) else {}
    return Posting(url=url, text=text, title=str(data.get("title") or ""),
                   company=str(data.get("company_name") or ""), location=str(location.get("name") or ""))


def fetch(url: str) -> Posting:
    # Ashby, Greenhouse, Lever and Workday publish the posting themselves, and a
    # captured URL is usually the application form rather than the description.
    # Ask the board before reading any page (upstream e2c600b).
    from . import boards
    found = boards.posting(url)
    if found is not None:
        return found
    with httpx.Client(
        follow_redirects=True,
        timeout=TIMEOUT,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
    ) as client:
        posting = _greenhouse(url, client)
        if posting is not None:
            return posting
        return _fetch_page(url, client)


def _fetch_page(url: str, client: httpx.Client) -> Posting:
    response = client.get(url)
    response.raise_for_status()
    html = response.text

    extractor = _TextExtractor()
    extractor.feed(html)
    text = extractor.text()

    posting = Posting(url=url, text=text)

    # Structured data beats the flattened page whenever the site provides it.
    ld = _job_posting_ld(extractor.ld_json)
    if ld:
        posting.title = str(ld.get("title") or "")
        org = ld.get("hiringOrganization")
        if isinstance(org, dict):
            posting.company = str(org.get("name") or "")
        location = ld.get("jobLocation")
        if isinstance(location, list) and location:
            location = location[0]
        if isinstance(location, dict):
            address = location.get("address")
            if isinstance(address, dict):
                posting.location = ", ".join(
                    str(address[key])
                    for key in ("addressLocality", "addressRegion", "addressCountry")
                    if address.get(key)
                )
        description = ld.get("description")
        if isinstance(description, str) and len(_strip_html(description)) > len(text):
            posting.text = _strip_html(description)

    if not posting.title:
        match = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
        posting.title = match.group(1).strip() if match else ""

    if len(posting.text) < MIN_USEFUL_CHARS:
        raise JavaScriptRequired(
            f"{url} returned {len(posting.text)} chars of text; "
            "the posting likely renders client-side or needs a login"
        )

    return posting
