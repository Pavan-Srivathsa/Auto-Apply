"""Lenny's Jobs (lennysjobs.com) as a Scout source.

The board is TrueUp's, and every row links straight to the employer's own
form (Ashby, Greenhouse, Lever, Workday, Amazon, ...) with `utm_source=
lennysjobs` on the end, so a role found here is queued like any other and
the fill handles it by its host. What is special is only how the list is
read: the rows are drawn by the page from a search API that answers guests
401, so the list is read the way a person reads it, in a tab of the
Autopilot Chrome, with the page's own filters (category, level) set and
"Show more" pressed until the rows stop growing. Nothing is submitted, no
account is used, and the tab is a background one that is closed after.

The page's filters are the whole selection: its default category is
Product Management, so the watch asks for another one. `Watch.args`
carries `category` and `levels`, defaulting to what a software new grad
wants. The titles then go through the same level filter as every watch.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from scout import Posting, Watch

URL = "https://www.lennysjobs.com/"
CATEGORY = "Engineering (Software)"
LEVELS = ["0 - Internship", "1 - Entry & mid-level"]
POSTED = "week"          # the page lists 50 rows to a guest; make them the fresh ones
POSTED_RADIO = {"day": 0, "week": 1, "month": 2, "all": 3}   # the page's "Date posted" order
MAX_ROWS = 300           # same horizon as the other providers
SETTLE = 12.0            # seconds the page gets to draw rows after a click
UTM = {"utm_source", "utm_medium", "utm_campaign", "ref"}

AGE = re.compile(r"^(?:(\d+)\s*(hour|day|week|month|year)s?|today|new)$", re.I)
SECONDS = {"hour": 3600, "day": 86400, "week": 7 * 86400, "month": 30 * 86400, "year": 365 * 86400}

# Real pointer events: Radix ignores a scripted `.click()` on its checkboxes.
RECT_JS = """(sel, text) => {
  const optionLabel = e => ((e.closest('li') || e).innerText || '').trim().split('\\n')[0].trim();
  const els = [...document.querySelectorAll(sel)].filter(e => text === null ||
    (e.getAttribute('aria-label') || optionLabel(e)) === text);
  const e = els[els.length - 1];
  if (!e) return null;
  e.scrollIntoView({block: 'center', behavior: 'instant'});
  const r = e.getBoundingClientRect();
  return {x: r.x + r.width / 2, y: r.y + r.height / 2,
          checked: e.getAttribute('aria-checked') === 'true'};
}"""

ROWS_JS = """(() => {
  const out = [];
  for (const a of document.querySelectorAll('a[href*="utm_source=lennysjobs"]')) {
    const title = a.textContent.replace(/\\u00a0/g, ' ').trim();
    if (!title) continue;
    let card = a;
    for (let i = 0; i < 8 && card.parentElement; i++) {
      card = card.parentElement;
      if (card.querySelector('a[href*="trueup.io/co/"]')) break;
    }
    const company = card.querySelector('a[href*="trueup.io/co/"]');
    const place = card.querySelector('.line-clamp-3');
    const age = [...card.querySelectorAll('span')].map(s => s.textContent.trim())
      .find(t => /^(\\d+\\s*(hour|day|week|month|year)s?|today|new)$/i.test(t)) || '';
    out.push({href: a.href, title, company: company ? company.textContent.trim() : '',
              location: place ? place.textContent.trim() : '', age});
  }
  return out;
})()"""

FILTER_JS = """(label) => {
  const e = [...document.querySelectorAll('button')].find(x => x.innerText.trim().startsWith(label));
  if (!e) return null;
  e.scrollIntoView({block: 'center', behavior: 'instant'});
  const r = e.getBoundingClientRect();
  return {x: r.x + r.width / 2, y: r.y + r.height / 2};
}"""

OPTIONS_JS = ("[...document.querySelectorAll('[role=dialog] button[role=checkbox]')].map(e => "
              "e.getAttribute('aria-label') || ((e.closest('li') || e).innerText || '').trim().split('\\n')[0].trim())")

# The category filter became single-select (2026-10-22): plain buttons in a
# list, one press picks one, and the trigger then shows the choice.
CHOICES_JS = ("[...document.querySelectorAll('[role=dialog] li button')].map(e => "
              "(e.innerText || '').trim().split('\\n')[0].trim()).filter(Boolean)")
CHOICE_RECT_JS = """(label) => {
  const e = [...document.querySelectorAll('[role=dialog] li button')]
    .find(x => (x.innerText || '').trim().split('\\n')[0].trim() === label);
  if (!e) return null;
  e.scrollIntoView({block: 'center', behavior: 'instant'});
  const r = e.getBoundingClientRect();
  return {x: r.x + r.width / 2, y: r.y + r.height / 2};
}"""
TRIGGER_JS = """(label) => {
  const e = [...document.querySelectorAll('button')].find(x => x.innerText.trim().startsWith(label));
  return e ? e.innerText.trim() : '';
}"""

SHOW_MORE_JS = """(() => {
  const rows = document.querySelectorAll('a[href*="utm_source=lennysjobs"]');
  const last = rows[rows.length - 1];
  // The filters have "Show more" buttons of their own; only the one under the rows counts.
  const b = [...document.querySelectorAll('button')].filter(x => x.textContent.trim() === 'Show more' &&
    last && (last.compareDocumentPosition(x) & Node.DOCUMENT_POSITION_FOLLOWING));
  const e = b[b.length - 1];
  if (!e) return null;
  e.scrollIntoView({block: 'center', behavior: 'instant'});
  const r = e.getBoundingClientRect();
  return {x: r.x + r.width / 2, y: r.y + r.height / 2};
})()"""


def clean_url(url: str) -> str:
    """The employer's own link without the board's tracking tags."""
    parts = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in UTM]
    return urlunparse(parts._replace(query=urlencode(query)))


def posted_date(age: str, now: datetime | None = None) -> str:
    """ISO date for "6 days" / "3 months" / "today"; "" when unreadable."""
    match = AGE.match(age.strip())
    if not match:
        return ""
    now = now or datetime.now(timezone.utc)
    if not match.group(1):
        return now.strftime("%Y-%m-%d")
    return (now - timedelta(seconds=int(match.group(1)) * SECONDS[match.group(2).lower()])).strftime("%Y-%m-%d")


GENERIC_HOST = {"www", "jobs", "job", "boards", "job-boards", "careers", "career", "apply", "hire", "recruiting"}


def company_from_url(url: str) -> str:
    """The employer a link names, for a row with no company link: the first
    part of the host ("accenture.wd103.myworkdayjobs.com"), else the board
    slug after a generic host ("jobs.ashbyhq.com/traba"). A job queued
    under the board's own name ("Lenny's Jobs") is worse than a guess."""
    parts = urlparse(url)
    first = parts.netloc.lower().split(".")[0]
    if first and first not in GENERIC_HOST:
        name = first
    else:
        path = [p for p in parts.path.split("/") if p and p not in ("embed", "en", "en-us")]
        name = path[0] if path else ""
    return name.replace("-", " ").title()


def to_postings(rows: list[dict]) -> list[Posting]:
    """Rows as the page showed them, newest first as the page sorts them,
    each employer URL once."""
    seen: set[str] = set()
    postings: list[Posting] = []
    for row in rows:
        url = clean_url(row["href"])
        if url in seen or not row.get("title"):
            continue
        seen.add(url)
        postings.append(Posting(id=url, title=row["title"], url=url, location=row.get("location", ""),
                                posted=posted_date(row.get("age", "")),
                                company=row.get("company") or company_from_url(url)))
    postings.sort(key=lambda p: p.posted, reverse=True)     # the page sorts by relevance
    return postings


async def _click(page, sel: str, text: str | None) -> dict | None:
    spot = await page.evaluate(f"({RECT_JS})({json.dumps(sel)}, {json.dumps(text)})")
    if spot:
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            await page.send("Input.dispatchMouseEvent", {"type": kind, "x": spot["x"], "y": spot["y"],
                                                         "button": "left", "clickCount": 1})
    return spot


async def _escape(page) -> None:
    for kind in ("keyDown", "keyUp"):
        await page.send("Input.dispatchKeyEvent", {"type": kind, "key": "Escape", "code": "Escape"})


async def _set_filter(page, button: str, keep: list[str]) -> None:
    """Open the filter button whose label starts with `button` and leave
    exactly `keep` ticked. Raises when an option is not there, which is the
    site having renamed it: a red BROKEN beats a quiet wrong list."""
    opened = await page.evaluate(f"({FILTER_JS})({json.dumps(button)})")
    if not opened:
        raise RuntimeError(f"Lenny's Jobs has no '{button}' filter any more")
    options: list[str] = []
    for attempt in range(2):        # the page may not have hydrated at the first press
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            await page.send("Input.dispatchMouseEvent", {"type": kind, "x": opened["x"], "y": opened["y"],
                                                         "button": "left", "clickCount": 1})
        for _ in range(10):
            await asyncio.sleep(0.5)
            options = await page.evaluate(OPTIONS_JS)
            if options:
                break
        if options:
            break
        await asyncio.sleep(2.0)
        opened = await page.evaluate(f"({FILTER_JS})({json.dumps(button)})") or opened
    if not options and len(keep) == 1:
        choices = await page.evaluate(CHOICES_JS)
        if keep[0] in choices:
            await _choose_one(page, button, keep[0])
            return
        raise RuntimeError(f"Lenny's Jobs '{button}' has no option {keep[0]!r} (it lists {choices})")
    for wanted in keep:
        if wanted not in options:
            raise RuntimeError(f"Lenny's Jobs '{button}' has no option {wanted!r} (it lists {options})")
    for label in options:
        spot = await page.evaluate(f"({RECT_JS})('[role=dialog] button[role=checkbox]', {json.dumps(label)})")
        if spot and spot["checked"] != (label in keep):
            await _click(page, "[role=dialog] button[role=checkbox]", label)
            await asyncio.sleep(0.6)
    await _escape(page)
    await asyncio.sleep(1.0)


async def _choose_one(page, button: str, wanted: str) -> None:
    """A single-select filter: press the option, then check the trigger now
    names it, since a press that missed would quietly list the wrong jobs."""
    spot = await page.evaluate(f"({CHOICE_RECT_JS})({json.dumps(wanted)})")
    for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
        await page.send("Input.dispatchMouseEvent", {"type": kind, "x": spot["x"], "y": spot["y"],
                                                     "button": "left", "clickCount": 1})
    for _ in range(10):
        await asyncio.sleep(0.5)
        if wanted in await page.evaluate(f"({TRIGGER_JS})({json.dumps(button)})"):
            break
    else:
        raise RuntimeError(f"Lenny's Jobs '{button}' did not take {wanted!r}")
    await _escape(page)
    await asyncio.sleep(1.5)


async def _set_posted(page, when: str) -> None:
    index = POSTED_RADIO.get(when)
    if index is None:
        raise RuntimeError(f"posted must be one of {sorted(POSTED_RADIO)}, not {when!r}")
    spot = await page.evaluate(
        "((i) => { const e = document.querySelectorAll('button[role=radio]')[i]; if (!e) return null;"
        " e.scrollIntoView({block:'center',behavior:'instant'}); const r = e.getBoundingClientRect();"
        f" return {{x: r.x + r.width/2, y: r.y + r.height/2}}; }})({index})")
    if not spot:
        raise RuntimeError("Lenny's Jobs has no 'Date posted' filter any more")
    for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
        await page.send("Input.dispatchMouseEvent", {"type": kind, "x": spot["x"], "y": spot["y"],
                                                     "button": "left", "clickCount": 1})
    await asyncio.sleep(1.5)


async def _count(page) -> int:
    return await page.evaluate("document.querySelectorAll('a[href*=\"utm_source=lennysjobs\"]').length")


async def read_rows(page, category: str, levels: list[str], posted: str = POSTED) -> list[dict]:
    """Filters set, rows drawn, "Show more" pressed until MAX_ROWS or no growth."""
    from browser import autofill
    # A background tab is throttled and the popovers never draw; telling the
    # page it has focus lets it work without taking the person's active tab.
    await page.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
    await autofill.wait_for_load(page)
    await asyncio.sleep(2.0)
    await _set_filter(page, "Job category", [category])
    if levels:
        await _set_filter(page, "Level", levels)
    await _set_posted(page, posted)
    await asyncio.sleep(2.0)
    count = await _count(page)
    while count < MAX_ROWS:
        spot = await page.evaluate(SHOW_MORE_JS)
        if not spot:
            break
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            await page.send("Input.dispatchMouseEvent", {"type": kind, "x": spot["x"], "y": spot["y"],
                                                         "button": "left", "clickCount": 1})
        grown, waited = count, 0.0
        while waited < SETTLE and grown == count:
            await asyncio.sleep(0.5)
            waited += 0.5
            grown = await _count(page)
        if grown == count:
            break
        count = grown
    return await page.evaluate(ROWS_JS)


async def _fetch(watch: Watch) -> list[Posting]:
    from browser import autofill, chrome
    url = chrome.cdp_url(chrome.port())
    if not chrome.is_listening(url):
        raise RuntimeError("Autopilot's Chrome is not running; Lenny's Jobs is read in it. Start Autopilot first.")
    args = watch.args or {}
    target = ""
    try:
        async with autofill.attached(url, URL, background=True) as (page, target):
            rows = await read_rows(page, args.get("category") or CATEGORY, list(args.get("levels", LEVELS)),
                                   args.get("posted") or POSTED)
    finally:
        if target:
            chrome.close_tab(target)
    return to_postings(rows)[:MAX_ROWS]


def fetch(watch: Watch, http=None) -> list[Posting]:
    """Provider entry: every role the page lists under the watch's filters."""
    return asyncio.run(_fetch(watch))
